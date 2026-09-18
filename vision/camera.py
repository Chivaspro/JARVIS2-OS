"""Shared, thread-safe OpenCV camera manager.

Single ownership model
----------------------
Exactly one :class:`CameraManager` exists per process (a module-level
singleton).  It is the *only* place in JARVIS that holds a live
``cv2.VideoCapture`` handle for continuous streaming.  The preview window,
the vision pipeline and one-shot capture helpers all pull frames from it,
which guarantees we never open the same webcam twice.

Design goals
------------
* Capture thread runs at the camera's native FPS and pushes the latest
  frame into a tiny ring buffer (old frames are discarded, never queued).
* Consumers pull the *latest* JPEG/ndarray via ``frame()`` / ``jpeg()`` —
  safe from any thread, no locks held during encoding of a stale frame
  thanks to an internal last-written-wins slot.
* Optional push callbacks (``subscribe()``) are throttled per consumer.
* Automatic re-probe + reconnect when the device drops (Windows cameras
  often disconnect after sleep / privacy toggles).
* ``take_photo()`` is a one-shot open-grab-release path used when the
  manager is NOT streaming, so on-demand captures never fight the stream.

Never blocks the event loop: this class is thread-native and the heavy
OpenCV work happens only inside its own daemon thread.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from vision.config import VisionConfig, load_vision_config
from vision.utils import logger

try:
    import cv2
    _CV2 = True
except Exception:  # pragma: no cover
    cv2 = None
    _CV2 = False

_ON_WINDOWS = sys.platform.startswith("win")


# ── Public small types ─────────────────────────────────────────────────────

FrameHandler = Callable[[bytes], None]       # receives JPEG bytes
StateHandler = Callable[[bool], None]         # receives running True/False


class CameraError(RuntimeError):
    pass


# ── Camera backend choice ──────────────────────────────────────────────────

def cv2_backend_for_os() -> int:
    if not _CV2:
        return 0
    try:
        if os.name == "nt":
            return cv2.CAP_DSHOW
        if sys.platform == "darwin":
            return cv2.CAP_AVFOUNDATION
        return cv2.CAP_ANY
    except AttributeError:
        return cv2.CAP_ANY


def read_frame(cap, warmup: int = 12) -> Tuple[bool, Optional[np.ndarray]]:
    """Read one good frame after a warm-up burst. Returns (ok, frame|None)."""
    try:
        for _ in range(warmup):
            cap.read()
        for _ in range(6):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                return True, frame
            time.sleep(0.02)
        return False, None
    except Exception:
        return False, None


# ── Camera index discovery ─────────────────────────────────────────────────

def probe_camera(index: int, backend: Optional[int] = None) -> bool:
    """Open `index`, grab a warm-up frame, confirm it is not pure black."""
    if not _CV2:
        return False
    backend = backend if backend is not None else cv2_backend_for_os()
    cap = None
    try:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            return False
        ok, frame = read_frame(cap)
        if not ok or frame is None:
            return False
        return bool(float(np.mean(frame)) > 8.0)
    except Exception:
        return False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def auto_detect_camera_index(preferred: Optional[int] = None,
                             max_probe: int = 6) -> int:
    """Return the first usable camera index.

    Tries the preferred index first (a fast, common path) before scanning
    0..max_probe-1.  Falls back to 0 when nothing answers.
    """
    order: List[int] = []
    pref = preferred
    if pref is not None and 0 <= pref < max_probe:
        order.append(pref)
    order.extend(i for i in range(max_probe) if i not in order)

    for idx in order:
        if probe_camera(idx):
            logger.info(f"camera found at index {idx}")
            return idx
    logger.warn("no usable camera found in probes — defaulting to index 0")
    return 0


def available_cameras(max_probe: int = 6) -> List[int]:
    """Indexes that currently open successfully (used by Settings → Vision)."""
    return [i for i in range(max_probe) if probe_camera(i)]


# ── The manager ────────────────────────────────────────────────────────────

class CameraManager:
    """Thread-native single-camera owner.

    When the camera is disabled or missing the manager simply reports
    ``running() == False`` and every method returns empty data — it never
    raises and never takes the application down with it.
    """

    def __init__(self, config: Optional[VisionConfig] = None):
        self._cfg = config or load_vision_config()
        self._lock = threading.RLock()
        self._subs: List[Tuple[FrameHandler, StateHandler, float, float]] = []
        self._capture_thread: Optional[threading.Thread] = None
        self._run_event = threading.Event()      # requested-running flag
        self._thread_alive = threading.Event()   # capture thread heartbeat
        self._stop_event = threading.Event()     # told to quit
        self._active_index: int = -1

        self._latest_bgr: Optional[np.ndarray] = None
        self._latest_jpg: bytes = b""
        self._latest_jpg_cv: Optional[np.ndarray] = None
        self._frame_seq = 0
        self._last_push = 0.0
        self._last_reader_ts = 0.0
        self._error_ts = 0.0
        self._last_cfg_ts = 0.0

        self._after_start: Optional[Callable[[], None]] = None
        self._on_state_callbacks: set = set()

    # ── public configuration ────────────────────────────────────────────────

    def reload_config(self, config: Optional[VisionConfig] = None) -> None:
        with self._lock:
            self._cfg = config or load_vision_config()

    @property
    def config(self) -> VisionConfig:
        with self._lock:
            return self._cfg

    @property
    def enabled(self) -> bool:
        return bool(self.config.camera_enabled)

    @property
    def running(self) -> bool:
        return self._thread_alive.is_set()

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def frame_sequence(self) -> int:
        return self._frame_seq

    # ── streaming ───────────────────────────────────────────────────────────

    def start(self, on_frame: Optional[FrameHandler] = None,
              on_state: Optional[StateHandler] = None) -> bool:
        """Start the shared capture thread.

        ``on_frame`` receives each latest JPEG frame at most once. It may be
        called from the capture thread. Returns True when the stream actually
        got going (or was already running).
        """
        if not _CV2:
            logger.error("OpenCV not installed — camera unavailable")
            return False
        if not self.enabled:
            logger.warn("camera disabled in config — not starting")
            return False

        if on_frame is not None or on_state is not None:
            self.subscribe(on_frame, on_state)

        with self._lock:
            if self._thread_alive.is_set():
                return True
            self._run_event.set()
            self._stop_event.clear()
            t = threading.Thread(target=self._capture_loop,
                                 daemon=True,
                                 name="jarvis-camera")
            self._capture_thread = t
            t.start()
        # Wait briefly for the first heartbeat so callers trust running().
        # Windows DSHOW can need many seconds on a cold open (observed 3-11s),
        # so allow up to 20s and require a latched frame before declaring ready.
        for _ in range(400):
            if self._thread_alive.is_set() and self.frame() is not None:
                break
            time.sleep(0.05)
        return self._thread_alive.is_set() and self.frame() is not None

    def stop(self) -> None:
        with self._lock:
            self._run_event.clear()
            self._stop_event.set()
        t = self._capture_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        with self._lock:
            self._latest_jpg = b""
            self._latest_bgr = None
            if self._capture_thread is not None:
                self._capture_thread = None

    # ── subscription helper ─────────────────────────────────────────────────

    def subscribe(self, on_frame: Optional[FrameHandler] = None,
                  on_state: Optional[StateHandler] = None,
                  max_fps: float = 30.0, cooldown: float = 0.0) -> None:
        """Register a push consumer. ``max_fps`` throttles local processing."""
        with self._lock:
            for i, (_, _, _, _) in enumerate(self._subs):
                pass
            self._subs.append((on_frame, on_state, max_fps, cooldown))

    def unsubscribe(self, on_frame: Optional[FrameHandler]) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s[0] is not on_frame]

    # ── frame access (any thread, never blocks on encoding) ────────────────

    def frame(self) -> Optional[np.ndarray]:
        """Latest BGR ndarray or None. Cheap copy form for readers."""
        with self._lock:
            latest = self._latest_bgr
            return latest.copy() if latest is not None else None

    def jpeg(self) -> bytes:
        with self._lock:
            return self._latest_jpg

    def frame_size(self) -> Tuple[int, int]:
        with self._lock:
            if self._latest_bgr is not None:
                return (self._latest_bgr.shape[1], self._latest_bgr.shape[0])
        return (0, 0)

    # ── capture loop ────────────────────────────────────────────────────────

    def _open_device(self, index: int):
        backend = cv2_backend_for_os()
        cap = cv2.VideoCapture(index, backend)
        return cap

    def _resolve_camera_index(self) -> int:
        cfg = self.config
        index = cfg.effective_camera_index()
        if index >= 0:
            return index
        # Auto mode: reuse a previously cached index when available, then probe.
        cached = None
        try:
            old = cfg.as_dict.get("camera_index", "auto")
            if str(old).lstrip("-").isdigit():
                cached = int(old)
        except Exception:
            cached = None
        return auto_detect_camera_index(cached if cached is not None else None)

    def _capture_loop(self) -> None:
        cfg = self.config
        want_w, want_h = cfg.camera_width, cfg.camera_height
        fps = max(5, min(60, cfg.camera_fps))
        interval = 1.0 / fps
        index = self._resolve_camera_index()

        attempts = 0
        cap = None
        while self._run_event.is_set():
            if cap is None or not cap.isOpened():
                try:
                    cap = self._open_device(index)
                except Exception:
                    cap = None
                if cap is None or not cap.isOpened():
                    attempts += 1
                    logger.warn(f"camera index {index} unavailable (attempt {attempts})")
                    if self._last_reader_ts == 0:
                        pass
                    self._error_ts = time.monotonic()
                    if not self._stop_event.wait(min(3.0, 0.75 * attempts)):
                        continue
                    break
                # Apply resolution where the driver honours it.
                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, want_w)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, want_h)
                    cap.set(cv2.CAP_PROP_FPS, fps)
                except Exception:
                    pass
            attempts = 0
            self._active_index = index
            self._thread_alive.set()
            self._error_ts = 0.0

            if not self._notify_state(True):
                pass

            first = True
            ok_seen = 0
            while self._run_event.is_set() and cap.isOpened():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    if first:
                        first = False
                    drop_ok = False
                    for _ in range(4):
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            drop_ok = True
                            break
                    if not drop_ok:
                        logger.warn("camera frame read failed — re-opening device")
                        break
                else:
                    ok_seen += 1

                if frame is not None and frame.size > 0:
                    self._good_frame(frame)

                # Enforce capture rate; reading faster than the FPS wastes CPU.
                elapsed = time.monotonic() - t0
                wait = interval - elapsed
                if wait > 0 and self._stop_event.wait(wait):
                    break

            # Loop end: release and maybe reconnect.
            self._thread_alive.clear()
            self._notify_state(False)
            try:
                cap.release()
            except Exception:
                pass
            cap = None
            if self._run_event.is_set():
                if self._stop_event.wait(1.0):
                    break
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        self._thread_alive.clear()
        self._notify_state(False)

    def _good_frame(self, frame: np.ndarray) -> None:
        cfg = self.config
        q = int(cfg.image_quality)
        jpg = encode_quick(frame, q)
        with self._lock:
            self._latest_bgr = frame
            self._latest_jpg = jpg
            self._frame_seq += 1
            now = time.monotonic()
            self._last_reader_ts = now
            subs = list(self._subs)
        for fn, _, max_fps, _ in subs:
            if fn is None:
                continue
            try:
                throttle = 1.0 / max(max_fps, 0.5) if max_fps > 0 else 0.0
                if throttle > 0 and (now - getattr(fn, "_last_push", 0.0)) < throttle:
                    continue
                fn(jpg)
                try:
                    fn._last_push = now
                except Exception:
                    pass
            except Exception as exc:
                logger.debug(f"frame consumer error: {exc}")

    def _notify_state(self, running: bool) -> bool:
        with self._lock:
            cbs = [s[1] for s in self._subs if s[1] is not None]
        for cb in cbs:
            try:
                cb(running)
            except Exception:
                pass
        return True

    # ── one-shot capture (no streaming) ─────────────────────────────────────

    def take_photo(self, max_probe: int = 6) -> Optional[np.ndarray]:
        """Grab a single BGR frame without starting the stream.

        If the stream is already running, simply returns its latest frame.
        Otherwise opens/releases the device like the legacy one-shot path.
        """
        if self._thread_alive.is_set():
            return self.frame()
        if not _CV2:
            return None
        cfg = self.config
        index = cfg.effective_camera_index()
        candidates = [index] if index >= 0 else []
        candidates += [i for i in range(max_probe) if i not in candidates]
        backend = cv2_backend_for_os()
        for idx in candidates:
            cap = None
            try:
                cap = self._open_device(idx)
                if not cap.isOpened():
                    continue
                ok, frame = read_frame(cap)
                if ok and frame is not None:
                    return frame
            except Exception:
                pass
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
        logger.error("take_photo: no usable frame from any camera")
        return None

    def screenshot_and_publish(self, jpg_handler: FrameHandler) -> bool:
        """Optional convenience: publish one frame to a handler."""
        frame = self.take_photo()
        if frame is None:
            return False
        jpg = encode_quick(frame, self.config.image_quality)
        try:
            jpg_handler(jpg)
        except Exception:
            pass
        return True


# ── module-level singleton ─────────────────────────────────────────────────

_GLOBAL: Optional[CameraManager] = None
_GLOBAL_LOCK = threading.Lock()


def get_camera_manager(config: Optional[VisionConfig] = None) -> CameraManager:
    """Return the process-wide single camera manager."""
    global _GLOBAL
    if config is not None:
        with _GLOBAL_LOCK:
            if _GLOBAL is None:
                _GLOBAL = CameraManager(config)
            else:
                _GLOBAL.reload_config(config)
            return _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = CameraManager(load_vision_config())
        else:
            # The caller did not pin a config: refresh from disk so settings
            # like camera_index/vision_model take effect on the next stream.
            try:
                _GLOBAL.reload_config(load_vision_config())
            except Exception:
                pass
        return _GLOBAL


def release_global_camera() -> None:
    """Stop + release the process-wide camera (used at shutdown)."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is not None:
            try:
                _GLOBAL.stop()
            except Exception:
                pass
            _GLOBAL = None


# ── tiny local helpers ─────────────────────────────────────────────────────

def encode_quick(frame_bgr: np.ndarray, quality: int = 82) -> bytes:
    try:
        ok, buf = cv2.imencode(".jpg", frame_bgr,
                               [cv2.IMWRITE_JPEG_QUALITY, int(max(20, min(95, quality)))])
        return buf.tobytes() if ok else b""
    except Exception:
        return b""