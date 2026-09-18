from __future__ import annotations

import platform as _platform
import subprocess as _subprocess

# ── Console encoding ─────────────────────────────────────────────────────────
# Windows consoles default to a legacy codepage — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# several of these prints sit inside except handlers, so the handler itself dies
# and skips the recovery code after it. Reconfiguring to UTF-8 with a
# replacement fallback costs nothing and makes the app behave in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import os
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
import random

# numpy is used by the mic level meter (_pcm_level) inside the audio callback;
# without this module-level import every callback raised NameError and the
# waveform never moved (the callback swallowed it as "cosmetic").
try:
    import numpy as np
except Exception:  # pragma: no cover - numpy ships with the app, but never fatal
    np = None


def _hide_windows_console():
    if _platform.system() != "Windows": return
    try:
        import ctypes
        hwnd=ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd: ctypes.windll.user32.ShowWindow(hwnd,0)
    except Exception: pass
_hide_windows_console()


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ── Startup profiling ────────────────────────────────────────────────────────
# time.perf_counter() markers that power the [STARTUP] ... boot report.
# They are print-only and never fatal, so they cannot break a release build.
_APP_TICK = time.perf_counter()

def _markup(tag: str) -> None:
    """Emit one monotonic elapsed-time marker since process start."""
    try:
        print(f"[STARTUP] {tag}  elapsed={time.perf_counter() - _APP_TICK:.3f}s")
    except Exception:
        pass


class _DeferredModules:
    """Background loader for the command-action import tail.

    Boot measures showed the action/tool modules (open_app, weather, browser,
    youtube, web_search, …) add ~1-2.5 s of pure import time that the Gemini
    Live connect never needs — commands can only run once a session exists.
    They are now imported on a daemon thread concurrently with the network
    connect, and the import tail is folded into the loader so no caller can
    ever see an unloaded name.
    """

    _MODULES = (
        "actions.file_processor", "actions.flight_finder", "actions.open_app",
        "actions.weather_report", "actions.send_message", "actions.reminder",
        "actions.computer_settings", "actions.screen_processor",
        "actions.youtube_video", "actions.desktop", "actions.browser_control",
        "actions.file_controller", "actions.code_helper", "actions.dev_agent",
        "actions.web_search", "actions.computer_control", "actions.game_updater",
        "actions.system_monitor", "actions.proactive", "actions.background_monitor",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._critical_evt = threading.Event()
        self._owner = None                  # JarvisLive, for client prewarm
        self._import_err: Exception | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="jarvis-deferred-imports", daemon=True
            )
            self._thread.start()

    def set_owner(self, owner) -> None:
        self._owner = owner

    def _owner_or_wait(self, timeout: float = 4.0):
        """Return the owner once set, waiting briefly for phase 3 timing.

        When the action tail finishes before JarvisLive exists, waiting a few
        seconds costs nothing (it overlaps the network connect) and lets the
        prewarm win the race instead of the run loop — the whole point of
        doing it in the background.
        """
        deadline = time.perf_counter() + timeout
        while self._owner is None and time.perf_counter() < deadline:
            time.sleep(0.1)
        return self._owner

    def wait_critical(self, timeout: float | None = None) -> bool:
        """Block until the AI-critical imports finished; re-raise on failure.

        A critical import failure is NOT optional — the old serial loader let
        it propagate into runner()'s error dialog, and so does this.
        """
        ok = self._critical_evt.wait(timeout)
        if self._import_err is not None:
            raise self._import_err
        return ok

    def _run(self) -> None:
        # Phase 1 — AI-critical core (audio, genai SDK, memory, config, gates).
        try:
            _load_backend_modules()
        except Exception as e:
            self._import_err = e
            self._critical_evt.set()   # wake the waiter; it re-raises
            print(f"[JARVIS] ⚠ Critical backend import failed: {e}")
            return
        self._critical_evt.set()

        # Phase 2 — prewarm the reusable genai.Client so the run loop finds it
        # already built. This MUST precede the action-tail imports: the client
        # is on the AI critical path (every ms here delays the connect), the
        # action tail is not. Same lock/fields as the run loop, so neither side
        # can observe a half-constructed client.
        owner = self._owner_or_wait()
        if owner is not None:
            try:
                from google import genai as _genai
                _key = _get_api_key()
                if _key:
                    _version = "v1alpha" if owner._enhanced_live else "v1beta"
                    with owner._client_lock:
                        if owner._client is None:
                            owner._client = _genai.Client(
                                api_key=_key, http_options={"api_version": _version}
                            )
                            owner._client_key = _key
                            owner._client_version = _version
                            print("[JARVIS] AI client prewarmed in background.")
            except Exception as e:
                print(f"[JARVIS] AI client prewarm skipped: {e}")

        # Phase 3 — command-action tail, concurrent with the network connect.
        try:
            for _mod in self._MODULES:
                __import__(_mod)
            # Fold the action-binding tail in here so the tool dispatcher can
            # never observe a partially-loaded module set.
            _load_action_modules()
        except Exception as e:  # a broken optional action module must not kill boot
            self._import_err = e
            print(f"[JARVIS] ⚠ Deferred action-module import failed: {e}")
        try:
            from google import genai as _genai
            _key = _get_api_key()
            if _key:
                _version = "v1alpha" if owner._enhanced_live else "v1beta"
                with owner._client_lock:
                    if owner._client is None:
                        owner._client = _genai.Client(
                            api_key=_key, http_options={"api_version": _version}
                        )
                        owner._client_key = _key
                        owner._client_version = _version
                        print("[JARVIS] AI client prewarmed in background.")
        except Exception as e:
            print(f"[JARVIS] AI client prewarm skipped: {e}")

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the loader finished; True when fully loaded."""
        th = self._thread
        if th is None:
            self.start()
            th = self._thread
        th.join(timeout)
        return not th.is_alive() and self._import_err is None


_deferred = _DeferredModules()

# ── Cached default SSL context ──────────────────────────────────────────────
# genai.Client() measured 3.3–4.8 s on Windows inside ssl context creation:
# google-genai builds one default context per transport (httpx sync, httpx
# async, aiohttp, websocket) and every build re-reads and re-parses the full
# certifi CA bundle (~0.5-0.75 s each) — and every RECONNECT builds a whole
# new client. The CA bundle cannot usefully change mid-process, so each
# distinct bundle (certifi file, or the platform store when no cafile is
# given) is loaded once and later default contexts are built from the cached
# PEM data. Every call still returns its own fresh context object, so caller
# mutations never leak; explicit cadata/capath/custom-purpose calls bypass
# the cache untouched.
def _patch_ssl_default_context_cache() -> None:
    try:
        import ssl as _ssl
        import base64 as _b64

        _orig_create = _ssl.create_default_context
        _bundles: dict = {}          # cafile-or-None -> PEM str (or False = uncachable)

        def _pem_from(ctx) -> str:
            parts = []
            for der in ctx.get_ca_certs(binary_form=True):
                b = _b64.b64encode(der).decode("ascii")
                lines = "\n".join(b[i:i + 64] for i in range(0, len(b), 64))
                parts.append(f"-----BEGIN CERTIFICATE-----\n{lines}\n-----END CERTIFICATE-----\n")
            return "\n".join(parts)

        def _create_default_context(purpose=_ssl.Purpose.SERVER_AUTH,
                                    cafile=None, capath=None, cadata=None):
            if (purpose != _ssl.Purpose.SERVER_AUTH or capath is not None
                    or cadata is not None):
                return _orig_create(purpose=purpose, cafile=cafile,
                                    capath=capath, cadata=cadata)
            key = cafile
            pem = _bundles.get(key)
            if pem is False:
                return _orig_create(purpose=purpose, cafile=cafile)
            if pem is None:
                try:
                    pem = _pem_from(_orig_create(purpose=purpose, cafile=cafile))
                except Exception:
                    pem = False
                _bundles[key] = pem
                if pem is False:
                    return _orig_create(purpose=purpose, cafile=cafile)
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)   # check_hostname + CERT_REQUIRED
            ctx.load_verify_locations(cadata=pem)
            return ctx

        _ssl.create_default_context = _create_default_context
    except Exception:
        pass                                    # never worth breaking boot over

_patch_ssl_default_context_cache()


# ── Cached file reads (mtime + size validation) ───────────────────────────────
# The Gemini connect path read api_keys.json five times, prompt.txt once and the
# memory store once per attempt — with zero caching. These helpers parse each
# file at most once and re-read only when the file actually changes on disk,
# so reconnects stop paying disk cost while edits are still picked up instantly.
_file_cache: dict[str, tuple] = {}
_file_cache_lock = threading.Lock()

def _read_cached_json(path: Path) -> dict:
    return _read_cached_store(path) or {}

def _read_cached_text(path: Path) -> str:
    return _read_cached_store(path) or ""

def _read_cached_store(path: Path):
    """Return cached parsed content for *path*; invalidates on mtime/size change."""
    with _file_cache_lock:
        return _read_cached_store_locked(path)

def _read_cached_store_locked(path: Path):
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _file_cache.pop(str(path), None)
        return None
    ckey = f"t:{path}"
    hit = _file_cache.get(ckey)
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8")
        raw: object = json.loads(text) if str(path).lower().endswith((".json", ".jsonc")) else text
    except Exception:
        _file_cache.pop(ckey, None)
        return None
    _file_cache[ckey] = (key, raw)
    return raw

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    if np is None:  # numpy missing — no meter, but the mic itself still works
        return 0.0
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))# ── granular permissions (Settings → Automation / Permissions) ──────────────
# The gate now lives in security/permissions.py so the tool dispatcher, the
# computer facade and the coding agents all share one implementation
# (change: modernize-jarvis-architecture, task 3.2). Behavior is identical:
# each tool (and, for multi-purpose tools, each action) is checked against its
# own Settings switch before any work happens, and a refusal names the switch.

def _permission_gate(enabled, name: str, args: dict) -> str | None:
    """Refusal sentence when a revoked Settings permission covers this tool.

    `enabled` is ui.feature_enabled (name -> bool). Returns None when the call
    may proceed. Never raises: a broken feature lookup must not block tools.
    """
    try:
        from security.permissions import permission_refusal
        return permission_refusal(enabled, name, args)
    except Exception:
        return None


def _get_api_key() -> str:
    data = _read_cached_json(API_CONFIG_PATH)
    return str(data.get("gemini_api_key", "") or "")


def _run_vision(ui, fn):
    """Run one vision coordinator call inside the executor thread."""
    from vision.manager import get_coordinator
    vc = get_coordinator(ui=ui)
    return fn(vc)


def _release_vision_resources():
    """Free the shared camera + co-ordinator at shutdown (idempotent)."""
    try:
        from vision.manager import release_coordinator
        release_coordinator()
    except Exception:
        pass
    try:
        from vision.camera import release_global_camera
        release_global_camera()
    except Exception:
        pass


def _log_vision_status():
    """Print vision subsystem readiness once at startup, non-fatal.

    Runs on a short-lived daemon thread: building the vision status pulls in
    mediapipe/torch and took ~9 s on the connected path, which delayed the audio
    tasks and the startup greeting. Vision probing must never gate READY.
    """
    def _work():
        try:
            from vision.config import vision_system_status
            status = vision_system_status()
            print(f"[Vision] camera={status.get('camera')} | "
                  f"objects={status.get('object_detection')} | "
                  f"faces={status.get('face_recognition')} | "
                  f"hands={status.get('hand_tracking')} | "
                  f"multimodal={status.get('multimodal_provider')} "
                  f"(key={status.get('provider_api_key')}) | device={status.get('device')}")
        except Exception as exc:
            print(f"[Vision] status unavailable: {exc}")
    try:
        threading.Thread(target=_work, daemon=True, name="vision-status").start()
    except Exception:
        pass


def _load_system_prompt() -> str:
    try:
        cached = _read_cached_text(PROMPT_PATH)
        if cached:
            return cached
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, an original premium AI executive assistant. "
            "Use refined British English with a calm, warm, precise butler-like delivery. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "generate_image",
        "description": (
            "Generates an AI image from a natural-language prompt and displays it inside the JARVIS UI. "
            "Use whenever the user asks to generate, create, draw, visualize, or make an AI image. "
            "Do not open an external image viewer."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Detailed description of the image to generate"},
                "aspect_ratio": {"type": "STRING", "description": "Image ratio such as 16:9, 1:1, or 9:16"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "play_local_video",
        "description": (
            "Plays a local media file INSIDE the JARVIS internal video panel. "
            "Use when the user asks to play/open a video from their computer, "
            "or says play the current/local video file. If path is omitted, use the "
            "currently selected file in JARVIS."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Optional absolute or relative path to the local media file"}
            },
            "required": []
        }
    },
    {
        "name": "jarvis_window",
        "description": (
            "Controls JARVIS's built-in HUD windows. Use this tool whenever the user asks to "
            "open, show, close, refresh, or search inside a JARVIS window such as WebView, "
            "Image Preview, World Monitor, Video Preview, Web Task, Memory Core, Activity Log, "
            "Content Surface, or 3D Display. For web/search requests, keep the result inside "
            "the JARVIS UI and do not open the external system browser when WebView is enabled. "
            "Examples: 'open webview', 'search cats in webview', 'open world monitor', "
            "'show image panel', 'open video preview', 'open memory core'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "window": {
                    "type": "STRING",
                    "description": "webview | image | world_monitor | video | web_task | memory | activity | content | 3d"
                },
                "action": {
                    "type": "STRING",
                    "description": "open | search | refresh | close"
                },
                "query": {
                    "type": "STRING",
                    "description": "Search query or URL, used by webview/search actions"
                }
            },
            "required": ["window"]
        }
    },
    {
        "name": "play_music",
        "description": (
            "Plays music in the JARVIS internal WebView. Use for requests such as "
            "play music, play a song, play an artist, play an album, or start music. "
            "When internal WebView is enabled, never open the system browser for music."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Song, artist, album, playlist, or music search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, 'show my desk' / 'desk view' "
            "(angle='screen'), 'open my camera' / 'open webcam' / 'show my camera' / "
            "'turn on camera' / 'open camera' / 'webcam view' / 'show yourself' "
            "(angle='camera'), who is there, what they hold or do, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "If a person is visible: count people, describe what each is doing, "
            "holding or wearing, and their position in the frame. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, hide camera, close webcam, "
            "stop camera, turn off camera, kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. "
            "restart, shutdown and toggle_wifi put a confirmation on the user's screen "
            "and do NOT happen until they press it — never claim they are done. "
            "Volume, brightness and dark mode can be reversed with the `undo` tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                # The exact vocabulary, spelled out.
                #
                # This used to say only "The action to perform", so the model
                # usually filled `description` instead — and computer_settings
                # then made a SECOND Gemini call, inside the tool, purely to
                # translate that sentence into one of these names. Every
                # "turn the volume down" cost two model round trips.
                "action": {
                    "type": "STRING",
                    "description": (
                        "The exact action. Prefer this over `description` — pick one of: "
                        "volume_up | volume_down | volume_set | mute | "
                        "brightness_up | brightness_down | sleep_display | "
                        "pause_video | close_app | close_window | full_screen | "
                        "minimize | maximize | snap_left | snap_right | "
                        "switch_window | show_desktop | task_manager | focus_search | "
                        "refresh_page | close_tab | new_tab | next_tab | prev_tab | "
                        "go_back | go_forward | zoom_in | zoom_out | zoom_reset | "
                        "find_on_page | scroll_up | scroll_down | scroll_top | "
                        "scroll_bottom | page_up | page_down | copy | paste | cut | "
                        "undo | redo | select_all | save | enter | escape | press_key | "
                        "type_text | screenshot | lock_screen | open_settings | "
                        "file_explorer | open_run | dark_mode | toggle_wifi | "
                        "restart | shutdown"
                    ),
                },
                "description": {
                    "type": "STRING",
                    "description": (
                        "Fallback only, when no action name above fits. "
                        "Resolved locally — no extra model call."
                    ),
                },
                "value":       {"type": "STRING", "description": "Optional value: volume level 0-100, text to type, key name, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": (
            "Direct computer control for desktop awareness, screen capture and "
            "screen understanding, mouse and keyboard control, clicking, typing, "
            "opening/focusing applications and windows, navigation, forms, "
            "copy/paste, terminal and system actions, file operations, and "
            "repetitive desktop automation. Use screen_find/screen_click for "
            "visual computer control."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "recall_memory",
        "description": (
            "Look up a fact you have stored about the user but which is NOT in "
            "the memory block of your system prompt. "
            "The prompt lists the keys it did not have room for under "
            "'[ALSO REMEMBERED]' — if the user asks about anything named there, "
            "call this FIRST. "
            "Also call it before saying you do not know something personal, and "
            "when the user asks what you remember about them (leave query empty "
            "for everything). "
            "This is a local file search: it is instant and costs nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Keyword to search for — a name, a topic, a category "
                        "(e.g. 'ayse', 'coffee', 'projects'). "
                        "Leave empty to list everything stored."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "brain_remember",
        "description": (
            "Store a fact, preference, correction or lifecycle event to the "
            "permanent JARVIS Brain and make it part of 'what I know'. Use for: "
            "user identity/profile, preferences, corrections ('remember I say X "
            "not Y'), things that should persist across sessions, and events "
            "worth recall later. Content is deduplicated, weighted by recency, "
            "and surfaced to the user in natural conversation. Prefer this over "
            "save_memory for anything the user asks you to 'remember'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "content": {
                    "type": "STRING",
                    "description": (
                        "The fact or preference to store, in plain English "
                        "(e.g. 'user prefers concise answers')"
                    ),
                },
                "category": {
                    "type": "STRING",
                    "description": (
                        "One of: PROFILE, PREFERENCES, PROJECTS, CONVERSATIONS, "
                        "EPISODES, TASKS, EVENTS, TOOLS, SOLUTIONS, ERRORS, "
                        "WORKFLOWS, ENVIRONMENT. Leave omitted for auto-detect."
                    ),
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "brain_forget",
        "description": (
            "Remove a previously stored Brain memory (or all of them). Call "
            "when the user says forget/remove/delete something I told you, or "
            "'forget everything' to clear all Brain memories."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Text to match stored memories against; empty clears "
                        "all stored memories."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "brain_summary",
        "description": (
            "Produce a compact summary of what the JARVIS Brain currently "
            "remembers, sorted by importance/relevance, or a learning report of "
            "what JARVIS has recently learned and tuned. Call when the user asks "
            "'what do you remember', 'what have you learned', or 'show me your "
            "memory'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Optional subject to filter memory summary by "
                        "(name, topic, category)."
                    ),
                },
                "report": {
                    "type": "STRING",
                    "description": (
                        "'memory' (default) — what you remember | "
                        "'learning' — what you have recently learned/adjusted"
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "undo",
        "description": (
            "Reverse the last change YOU made to this computer — a file you "
            "moved, renamed, created or wrote, or a setting you changed such as "
            "volume, brightness, dark mode or WiFi. "
            "Call this whenever the user says undo, revert, take it back, put it "
            "back, cancel that, or tells you that you did the wrong thing, in ANY "
            "language. "
            "Use action='list' when they ask what can be undone. "
            "This only covers your own actions — it is not the Ctrl+Z of whatever "
            "application is on screen (that is computer_settings with action 'undo')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
            "required": [],
        },
    },
    {
        "name": "vision_look",
        "description": (
            "Take a live photo with the webcam and describe what is in front of "
            "the computer. Use when the user asks 'what do you see?', 'look at "
            "me', 'describe the scene', 'what is around me?', or wants a real "
            "description of the room / camera view. Returns a short spoken-style "
            "sentence."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {
                    "type": "STRING",
                    "description": "Optional specific question about the scene (default: describe briefly).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "recognize_faces",
        "description": (
            "Detect faces in the webcam view, resolve them against the local "
            "identity database, and report heads/families. Use for 'who is "
            "there?', 'who am I looking at?', 'how many people are in front of "
            "you?', 'is anyone standing behind me?' Returns names for registered "
            "faces (e.g. 'Alex') or 'an unknown person'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "what_am_i_holding",
        "description": (
            "Identify the object the user is holding in front of the camera "
            "(gesture + object + multimodal recognition). Use for 'what am I "
            "holding?', 'what's in my hand?', 'guess what I'm holding', "
            "'what is this?'. Returns e.g. 'a black smartphone'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "detect_objects",
        "description": (
            "Run object detection on the live camera view and report what "
            "objects are visible (a person, a phone, a book…) and roughly where. "
            "Use for 'what objects can you see?', 'what is on the desk?', "
            "'how many people are there?'. Returns a compact list."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "detect_gesture",
        "description": (
            "Classify the hand gesture the user is currently making towards the "
            "camera — open palm, fist, pointing, thumbs up, thumbs down, peace "
            "sign, OK, pinch, pinch-in/out, swipe. Use for 'what gesture am I "
            "making?', 'guess my hand sign'. Returns the recognized name with "
            "confidence."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
]

# ── Skill registry + task memory tools ─────────────────────────────────────
# Declared separately so the large block above stays untouched; appended so the
# live model sees them exactly like any other tool.
TOOL_DECLARATIONS += [
    {
        "name": "diagnostics",
        "description": (
            "Run JARVIS's own health checks and tool inventory. Use it for "
            "'run diagnostics', 'check voice/memory/camera/browser/settings', "
            "'what tools do I have', 'why is JARVIS broken', 'fix my settings', "
            "'what changed', 'restore the last good configuration', and safe "
            "mode. Reports only what was actually checked."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": (
                        "all | check | tools | settings | models | repair | "
                        "restore | what_changed | backups | safe_mode | "
                        "clear_safe_mode"
                    )
                },
                "target": {
                    "type": "STRING",
                    "description": (
                        "For mode=check: ai | voice | memory | wake_word | camera "
                        "| vision | browser | tools | plugins | filesystem | "
                        "system | configuration | safe_mode. For mode=restore: "
                        "a backup file name (optional)."
                    )
                },
                "probe_camera": {
                    "type": "BOOLEAN",
                    "description": "Open the camera to prove it works (default false)."
                }
            },
            "required": ["mode"]
        }
    },
    {
        "name": "performance_scan",
        "description": (
            "Diagnose why the computer feels slow: measures CPU, RAM, swap, GPU, "
            "disk and network, classifies the dominant bottleneck, lists the "
            "busiest processes and scans JARVIS itself. Use for 'my computer is "
            "lagging', 'fix the lag', 'why is my PC slow', 'performance scan', "
            "'what is slowing us down'. It NEVER changes anything — it reports "
            "and suggests safe next steps for the user to approve."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": "scan (default, full diagnosis) | processes | jarvis | history"
                },
                "scope": {
                    "type": "STRING",
                    "description": "all (default) | quick | processes | jarvis"
                }
            },
            "required": ["mode"]
        }
    },
    {
        "name": "task_plan",
        "description": (
            "Multi-step task memory. Use it for any job that needs three or more "
            "dependent actions, and whenever a job gets interrupted. It stores the "
            "goal, the ordered steps, what is already done, what failed and what is "
            "waiting on the user, so work can continue later without starting over. "
            "Actions: 'start' (open a plan with goal + steps), 'done' (a step "
            "finished), 'fail' (a step failed), 'block' (the user must do something "
            "first — log in, solve a CAPTCHA, choose an option), 'resume' (what is "
            "left), 'finish' (the whole job is verified complete), 'abandon', "
            "'list', 'status', 'note', 'file'. Do not use it for single-step requests."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description":
                           "start | done | fail | block | resume | finish | abandon | list | status | note | file"},
                "goal":   {"type": "STRING", "description": "What the user actually wants (action='start')"},
                "steps":  {"type": "ARRAY", "items": {"type": "STRING"},
                           "description": "Ordered steps of the plan (action='start')"},
                "step":   {"type": "STRING", "description": "The step being reported or resolved"},
                "task_id": {"type": "INTEGER", "description": "Task number from the TASK MEMORY block; omit for the current one"},
                "reason": {"type": "STRING", "description": "Why it is blocked, or why the task was dropped"},
                "error":  {"type": "STRING", "description": "What failed and the observable evidence"},
                "detail": {"type": "STRING", "description": "Short note, decision or result"},
                "note":   {"type": "STRING", "description": "A decision worth remembering for this task"},
                "summary":{"type": "STRING", "description": "Outcome recorded when finishing the task"},
                "files":  {"type": "ARRAY", "items": {"type": "STRING"},
                           "description": "Files involved in this task"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "skill_query",
        "description": (
            "Looks up your own capabilities from the skill registry. Use it when the "
            "user asks what you can do, whether you are able to do something, or which "
            "approach fits a request. Returns the matching skills, the tools behind "
            "them and any configuration they need, so you never promise something "
            "that is switched off."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":    {"type": "STRING", "description": "The task or capability to look up, e.g. 'upload a video'"},
                "list_all": {"type": "BOOLEAN", "description": "True to list every available skill"},
            },
            "required": [],
        },
    },
]


class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVIS"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        # Local (non-Gemini) voice: one playback at a time, and a stop request
        # that must also silence the audio device — not just the Gemini queue.
        self._local_tts_lock      = threading.Lock()
        self._local_tts_stop      = False
        self._tts_player          = None
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle

        # ── JARVIS Brain (persistent learning layer) ────────────────
        # Lazily constructed; any failure degrades to a no-op so the assistant
        # always boots. See core/brain_bridge (additive; never breaks voice).
        self._brain = None            # filled on first prompt build
        self._last_tool_ok: dict[str, bool] = {}   # tool name → last outcome

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._dashboard_state  = "off"           # off → building → ready | off
        self._briefing_sent    = False          # morning briefing fires once per process
        # SystemMonitor/ProactiveEngine are only touched by post-connect
        # background tasks, so building them here made JARVIS construction pay
        # for psutil-heavy init before the AI was even configured. Built on
        # first use instead (see the _sys_monitor/_proactive properties).
        self._sys_monitor_impl: "object | None" = None
        self._proactive_impl:   "object | None" = None
        self._bg_init_lock      = threading.Lock()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary

        self._enhanced_live = True  # affective dialog + proactive audio; auto-disabled if the server rejects them
        # Reused genai.Client — a thin authenticated handle, so one is enough for
        # every reconnect. Recreated only when the key or API version changes.
        self._client: "object | None" = None
        self._client_key       = ""
        self._client_version   = ""
        # Guards client construction between the deferred-loader prewarm thread
        # and the run loop, so neither side can observe a half-built client.
        self._client_lock = threading.Lock()
        self._marked_first_audio  = False
        self._marked_first_speech = False
        self._first_response_marked = False
        _core_names = {t["name"] for t in TOOL_DECLARATIONS}
        _markup("PLUGIN_DISCOVERY_BEGIN")
        self._plugin_registry = discover_plugins(
            plugins_dir=Path(__file__).resolve().parent / "plugins",
            core_tool_names=_core_names,
            logger=lambda msg: (print(f"[Plugins] {msg}"), self.ui.write_log(f"SYS: {msg}")),
        )
        _markup("PLUGIN_DISCOVERY_DONE")
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── Voice output mode + Piper speech service ────────────────────────
        # ONE resolved value decides who renders the audible reply, and it is
        # read from the single source of truth (normalize_voice_output_mode).
        #
        # The legacy HybridVoiceSystem is deliberately NOT started any more: it
        # opened its own microphone (a second InputStream, i.e. duplicate mic
        # capture) and rendered nothing at all, because no reply text was ever
        # routed to a voice. gemini_piper replaces it and owns no input device.
        self._voice_mode = "gemini"
        self._piper = None            # PiperSpeechService when mode == gemini_piper
        try:
            from core.hybrid_voice import resolve_voice_mode
            _cfg = _read_cached_json(API_CONFIG_PATH) or {}
            _mode, _needs_marker = resolve_voice_mode(_cfg)
            if _needs_marker:
                print("[JARVIS] Voice pipeline migrated: gemini -> gemini_piper "
                      "(Gemini Live reply text rendered by Piper ONNX). Choose "
                      "Gemini Native Audio in Settings > Voice to go back.")
            self._apply_voice_mode(_cfg, announce=False)
            print(f"[JARVIS] 🎙 Voice output mode: {_mode}")
        except Exception as _vm_exc:
            print(f"[JARVIS] ⚠ Voice mode setup failed: {_vm_exc}")

    # ── Voice output mode: gemini | gemini_piper | local ────────────────────

    def voice_mode(self) -> str:
        """The one place the renderer is decided (re-read from config so a
        Settings save takes effect without a restart). Never raises.

        Provider-aware: the ACTIVE provider decides — gemini renders through
        Gemini (native or Piper per the renderer setting), any other provider
        is the local engine. Legacy mode keys still resolve when no provider
        block is present.
        """
        try:
            from core.hybrid_voice import resolve_active_mode
            return resolve_active_mode(_read_cached_json(API_CONFIG_PATH) or {})
        except Exception:
            return getattr(self, "_voice_mode", "gemini") or "gemini"

    def _apply_voice_mode(self, cfg: dict | None = None, announce: bool = True) -> str:
        """Start/stop Piper for the resolved mode. Never raises.

        gemini       → Gemini's own audio is the voice (untouched behaviour).
        gemini_piper → Piper renders the reply from Gemini's transcript text.
        local        → the locally selected TTS engine speaks at turn end.
        """
        cfg = dict(cfg if cfg is not None else (_read_cached_json(API_CONFIG_PATH) or {}))
        mode = self.voice_mode()
        self._voice_mode = mode
        try:
            from core.hybrid_voice import PiperSpeechService
        except Exception as exc:
            print(f"[JARVIS] ⚠ Piper service unavailable: {exc}")
            return mode
        try:
            if mode == "gemini_piper":
                service = PiperSpeechService.get(cfg)
                service.on_state(self._on_piper_state)
                service.warmup_async()      # background: never blocks the UI
                self._piper = service
                if announce:
                    self.ui.write_log(
                        "SYS: Voice output — Gemini Live + Piper ONNX "
                        "(native Gemini audio suppressed).")
                print("[JARVIS] 🗣 Pipeline: mic → Gemini Live → reply text → "
                      "Piper ONNX (jarvis-high) → speaker")
            else:
                if self._piper is not None:
                    # Leaving Piper mode must silence it and drop its queue, or a
                    # stale reply would still start speaking after the switch.
                    self._piper.interrupt()
                    self._piper = None
                if announce:
                    label = ("Gemini native audio" if mode == "gemini"
                             else "local engine (Settings ▸ Voice)")
                    self.ui.write_log(f"SYS: Voice output — {label}.")
        except Exception as exc:
            print(f"[JARVIS] ⚠ Voice mode apply failed: {exc}")
        return mode

    def _on_piper_state(self, phase: str) -> None:
        """Piper phases → the existing HUD states (so no visual change is needed).

        While Piper speaks the mic is not forwarded to Gemini (set_speaking),
        exactly like local-voice mode: that keeps Gemini from hearing its own
        reply through the speakers. Barge-in still works because the wake-word
        detector is fed straight from the mic callback, before that gate.
        """
        try:
            if phase == "synthesizing":
                self.set_speaking(True)
                self.ui.set_state("PIPER SYNTHESIZING")
            elif phase == "playing":
                self.set_speaking(True)
                self.ui.set_state("PIPER PLAYING")
            elif phase in ("idle", "error"):
                self.set_speaking(False)
                if phase == "error":
                    self.ui.write_log("SYS: Piper voice error — see log.")
        except Exception:
            pass

    @property
    def _sys_monitor(self):
        """Lazy SystemMonitor — first touch imports/builds once, then caches."""
        if self._sys_monitor_impl is None:
            with self._bg_init_lock:
                if self._sys_monitor_impl is None:
                    # Direct import (sys.modules hit after the deferred loader
                    # ran) so this never depends on global binding order.
                    from actions.system_monitor import SystemMonitor as _SM
                    self._sys_monitor_impl = _SM()
        return self._sys_monitor_impl

    @property
    def _proactive(self):
        """Lazy ProactiveEngine — same rationale as _sys_monitor."""
        if self._proactive_impl is None:
            with self._bg_init_lock:
                if self._proactive_impl is None:
                    from actions.proactive import ProactiveEngine as _PE
                    self._proactive_impl = _PE()
        return self._proactive_impl

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = ""):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        if loop and ev is not None:
            loop.call_soon_threadsafe(ev.set)

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        # Resolve the newly saved mode and start/stop Piper for it immediately:
        # this silences Piper (and clears its queue) when leaving Piper mode, and
        # preloads it when entering. The rebuilt session picks the change up for
        # the next reply; nothing here blocks the Qt thread.
        try:
            self._apply_voice_mode(announce=True)
        except Exception as exc:
            print(f"[JARVIS] ⚠ Voice mode change failed: {exc}")
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        self.request_reconnect(keep_context=True, reason="audio device")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        self.ui.write_log(
            f"SYS: Applying {reason} — reconnecting"
            + ("..." if keep else " (starting a fresh conversation)...")
        )
        raise _ReconnectSignal(keep_context=keep)

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            if getattr(self, "_dashboard_state", "off") == "building":
                self.ui.write_log(
                    "SYS: Remote Dashboard is still starting — try again in a few seconds."
                )
            else:
                self.ui.write_log(
                    "SYS: Dashboard unavailable. "
                    "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
                )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        # Typed input joins the same conversation ledger as speech, so a typed
        # command can refer back to something said aloud (and the reverse).
        try:
            from core.brain_bridge import observe_utterance
            observe_utterance(str(text))
        except Exception:
            pass
        # Non-Gemini AI providers answer typed text directly over HTTP —
        # voice input still flows through the Gemini live session.
        try:
            from core import ai_providers as _aip
            _cfg = _read_cached_json(API_CONFIG_PATH) or {}
            if str(_cfg.get("ai_provider", "gemini") or "gemini").strip().lower() != "gemini":
                threading.Thread(
                    target=self._answer_via_provider, args=(str(text), dict(_cfg)), daemon=True
                ).start()
                return
        except Exception:
            pass
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _answer_via_provider(self, text: str, cfg: dict) -> None:
        """Synchronous worker: ask the configured provider, show + speak the reply."""
        try:
            from core import ai_providers as _aip
            active = _aip.active_config(cfg)
            label = active.get("name", active["provider"]) if active["provider"] == "custom" else active["provider"]
            self.ui.write_log(f"SYS: Asking {label} ({active.get('model', '?')})…")
            reply = _aip.chat(cfg, text)
        except Exception as exc:
            reply = f"I'm afraid the {cfg.get('ai_provider', '?')} link failed: {exc}"
        self.ui.write_log(f"JARVIS: {reply}")
        self._session_log.append(f"assistant: {reply}")
        try:
            from core.brain_bridge import record_reply
            record_reply(reply)
        except Exception:
            pass
        # Speak the reply through the shared local-voice path (serialised, and
        # it drives the HUD speaking state).
        try:
            self.speak_local(reply)
        except Exception as exc:
            print(f"[Provider] TTS failed: {exc}")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            if not self._marked_first_speech:
                self._marked_first_speech = True
                _markup("FIRST_SPEECH")
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")
        try:
            self.ui.set_brain_activity("speaking" if value else "idle")
        except Exception:
            pass

    def interrupt(self, source: str = "manual") -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately.

        `source` is "manual" for the ESC key / ✋ button and "voice" for the
        wake-word barge-in. A voice-driven stop obeys Settings → Automation →
        Voice interruption; an explicit manual stop is a priority-1 command and
        is never refused.
        """
        if source == "voice":
            try:
                _feats = (_read_cached_json(API_CONFIG_PATH) or {}).get("features", {}) or {}
                if not bool(_feats.get("voice_interruption", True)):
                    return
            except Exception:
                pass
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        # Local TTS plays through its own device, so draining the Gemini queue
        # is not enough: silence the engine itself and flag any line still
        # waiting so it does not start after the stop.
        self._local_tts_stop = True
        _player = getattr(self, "_tts_player", None)
        if _player is not None:
            try:
                _player.stop()
            except Exception:
                pass
        # Piper plays through its own long-lived worker, so silencing the engine
        # is not enough: the queue must be cleared too, or the rest of the
        # interrupted reply would start speaking again a moment later.
        _piper = getattr(self, "_piper", None)
        if _piper is not None:
            try:
                _piper.interrupt()
            except Exception:
                pass
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_local(self, text: str) -> None:
        """Speak one line through the configured local TTS engine (blocking;
        call from a worker thread). Never raises.

        Serialised: two replies used to start their own playback thread and
        talked over each other. Playback also owns the HUD speaking state for
        its whole duration — in local voice mode nothing else knows when sound
        is coming out, so the reactor showed LISTENING while JARVIS spoke.
        """
        text = (text or "").strip()
        if not text:
            return
        with self._local_tts_lock:
            # A stop request arrived while this line was queued: drop it instead
            # of talking after the user asked for silence.
            if self._local_tts_stop:
                self._local_tts_stop = False
                return
            try:
                from core.tts import create_tts_player
                player = create_tts_player(_read_cached_json(API_CONFIG_PATH) or {})
            except Exception as exc:
                print(f"[JARVIS] Local voice failed: {exc}")
                return
            self._tts_player = player
            self.set_speaking(True)
            try:
                player.speak(text)
            except Exception as exc:
                print(f"[JARVIS] Local voice failed: {exc}")
            finally:
                self._tts_player = None
                self.set_speaking(False)

    def _drain_audio_queue(self) -> None:
        try:
            while True:
                self.audio_in_queue.get_nowait()
        except Exception:
            pass

    def prewarm_voice(self):
        """Build the configured TTS engine in the background so the startup
        greeting speaks with zero engine-init delay. Purely optional work:
        any failure is logged and ignored (the first speak() would otherwise
        do the same init inline)."""
        def _warm():
            try:
                from core.tts import create_tts_player
                cfg = _read_cached_json(API_CONFIG_PATH) or {}
                player = create_tts_player(cfg)
                _mode = self.voice_mode()
                # gemini_piper: the greeting is spoken by Gemini → Piper, so the
                # only warm-up needed here is Piper's own voice — in the
                # background, reusing the loaded model, never blocking the UI.
                if _mode == "gemini_piper":
                    from core.hybrid_voice import PiperSpeechService
                    PiperSpeechService.get(cfg).warmup_async()
                    print("[STARTUP] PIPER_PREWARM_STARTED")
                    return
                # Local-voice mode: the startup greeting plays through the
                # user's chosen engine (onnx/sapi/edgetts…) instead of the
                # Gemini voice — speak it directly here.
                if _mode == "local":
                    hour = datetime.now().hour
                    addr = (cfg.get("user_name") or "").strip() or "sir"
                    g = (f"Good morning, {addr}. All systems online."
                         if 5 <= hour < 12 else
                         f"Good afternoon, {addr}." if 12 <= hour < 18 else
                         f"Good evening, {addr}." if 18 <= hour < 22 else
                         f"Good night, {addr}.")
                    threading.Thread(target=player.speak, args=(g,), daemon=True).start()
                else:
                    player.warmup()
                print("[STARTUP] VOICE_PREWARMED")
            except Exception as exc:
                print(f"[STARTUP] voice prewarm skipped: {exc}")
        threading.Thread(target=_warm, name="tts-prewarm", daemon=True).start()

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config (mtime-cached — one parse, instant picks-up)
        try:
            _cfg = _read_cached_json(API_CONFIG_PATH)
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""

        _markup("MEMORY_LOAD_BEGIN")
        memory     = load_memory()
        _markup("MEMORY_LOAD_DONE")
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()
        _markup("AI_PROMPT_BUILD")

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = (f"ADDRESS: Always call the user '{_user_name}' in English."
                 if _user_name
                 else "ADDRESS: Always address the user as \"sir\" in English. "
                      "Never an archaic or aristocratic form, and never a form "
                      "from another language.")
        lang_ctx = (
            "[LANGUAGE — HIGHEST PRIORITY]\n"
            "Always speak English. Every reply is in clear refined English, "
            "no matter what language the user writes or speaks, what memory "
            "says, or what language a tool result is in. Never switch "
            "languages mid-conversation. This overrides any other language "
            "instruction.\n\n"
        )
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )
        voice_mode = str(_cfg.get("voice_mode", "standard")).strip().lower()
        voice_ctx = (
            "[VOICE DELIVERY]\n"
            f"Use the {voice_mode} delivery mode. Keep the same refined British "
            "English identity: natural rhythm, clear pronunciation, calm confidence, "
            "and understated professionalism. Speak like an original premium British "
            "executive butler: warm, composed, concise, quietly witty when appropriate, "
            "and never theatrical or robotic. Address the user as sir unless a saved "
            "name is provided. Do not imitate any actor or copyrighted performance, "
            "and do not mention this instruction.\n\n"
        )

        parts = [time_ctx, lang_ctx, identity_ctx, voice_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        # JARVIS Brain context — persistent personality, learning nudges and
        # honest knowledge-boundary note. Additive; skipped on any failure.
        try:
            if self._brain is None:
                from core.brain_bridge import get_brain
                self._brain = get_brain()
            if self._brain is not None:
                from core.brain_bridge import augment_system_prompt
                parts = augment_system_prompt(parts)
        except Exception:
            pass

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS + self._plugin_registry.get_tool_declarations()}],
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        try:
            _feat = _read_cached_json(API_CONFIG_PATH).get("features", {})
            _proactive_voice = bool((_feat or {}).get("proactive_speaking", False))
        except Exception:
            _proactive_voice = False
        _markup("AI_TOOL_REGISTRATION")
        if self._enhanced_live:
            # Affective dialog: JARVIS hears tone/emotion and adapts its voice.
            cfg["enable_affective_dialog"] = True
            # Server-side proactive speech ONLY when the user opted in.
            # Default: JARVIS never speaks unless spoken to first.
            if _proactive_voice:
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        return types.LiveConnectConfig(**cfg)

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        # A tool call proves the session is live, so the deferred action-module
        # import must be complete before the dispatcher reads its globals.
        _deferred.wait()

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        try:
            self.ui.set_brain_activity("thinking")
        except Exception:
            pass

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        async def _brain_tool(name_, args_):
            """Run a brain_* tool through the Brain bridge (thread-safe)."""
            from core.brain_bridge import get_brain
            b = get_brain()
            if b is None:
                return "Brain unavailable."
            if name_ == "brain_remember":
                content = str(args_.get("content", "")).strip()
                if not content:
                    return "Nothing to remember — content was empty."
                cat = str(args_.get("category") or "").strip().upper() or None
                mid = await loop.run_in_executor(None, lambda: b.memory.remember(content, category=cat))
                return f"Remembered: {content}"
            if name_ == "brain_forget":
                query = str(args_.get("query", "") or "").strip()
                removed = await loop.run_in_executor(None, b.memory.forget_query, query)
                return (f"Forgot {removed} matching brain memory/ies."
                        if query else "All brain memories cleared.")
            if name_ == "brain_summary":
                report = str(args_.get("report", "memory") or "memory").strip().lower()
                query  = str(args_.get("query", "") or "").strip() or None
                if report in ("learning", "learned", "report"):
                    return await loop.run_in_executor(None, b.memory.learned_report)
                if query:
                    return await loop.run_in_executor(None, lambda: b.memory.recall_text(query, limit=15))
                return await loop.run_in_executor(None, b.memory.summary)
            return "Unknown brain tool."

        loop   = asyncio.get_event_loop()
        result = "Done."

        async def _computer(name_, args_):
            """Run a GUI tool through the ComputerUse facade.

            One path for every computer tool, so the selected backend (the Cua
            driver when it is reachable, the local controller otherwise), the
            serialization lock and the action log all apply. Without this the
            dispatcher would call the local automation functions directly and no
            other backend could ever be primary.
            """
            from computer.computer_use import get_computer_use, ComputerActionError

            def _run():
                return get_computer_use().execute(
                    name_, args_, response=None, player=self.ui, task_id=_corr,
                )

            try:
                return await loop.run_in_executor(None, _run)
            except ComputerActionError as exc:
                # The concise line is what the model and the user hear; the
                # multi-line diagnosis (backend, target, reason, next action)
                # goes to the on-screen log so a failure is attributable.
                try:
                    self.ui.write_log("[computer] " + exc.detail.replace("\n", " | "))
                except Exception:
                    pass
                return exc.summary
        # Real tool work starts here — reactor switches to EXECUTING until
        # the result comes back and speech/idle states take over again.
        try:
            self.ui.set_state("EXECUTING")
        except Exception:
            pass

        _tool_ok = True
        _tool_skip_learning = False
        # Correlation id per tool call (spec §55-§56) so a failure in the log can
        # be traced to the exact call that produced it.
        _corr = ""
        try:
            from core.diagnostics import new_correlation
            _corr = new_correlation()
        except Exception:
            _corr = ""
        try:
            # One permission check ahead of the whole chain: a revoked switch
            # must stop the action, not just be recorded in the config file.
            _denied = _permission_gate(self.ui.feature_enabled, name, args)
            if _denied:
                result = _denied
                # A refused permission is NOT a tool failure: skip the learning
                # feed, or every blocked call would drag this tool's success
                # rate down and change which tool JARVIS prefers.
                _tool_skip_learning = True
                print(f"[Perms] {name} blocked: {_denied}")

            elif name.startswith("brain_"):
                result = await _brain_tool(name, args)

            elif name == "diagnostics":
                # Off the event loop: tool discovery runs `--version` on every
                # external binary (up to 6 s each when one hangs), and the
                # system check samples CPU for 250 ms. On the loop thread that
                # would stall audio and the UI.
                from core.diagnostics import handle_diagnostics
                result = await loop.run_in_executor(
                    None, lambda: handle_diagnostics(args))
                print(f"[Diag] {str(result).splitlines()[0][:120] if result else ''}")

            elif name == "performance_scan":
                # psutil sampling sleeps ~1 s per scan — keep it off the loop
                # like diagnostics, or audio would stutter while measuring.
                from core.performance import handle_performance
                result = await loop.run_in_executor(
                    None, lambda: handle_performance(args))
                print(f"[Perf] {str(result).splitlines()[0][:120] if result else ''}")

            elif name in ("task_plan", "skill_query"):
                # Skill registry + task memory live in brain/ and touch sqlite,
                # so run them off the event loop like the other stateful tools.
                from core.brain_bridge import handle_skill_query, handle_task_command
                if name == "task_plan":
                    result = await loop.run_in_executor(
                        None, lambda: handle_task_command(args))
                else:
                    result = await loop.run_in_executor(
                        None, lambda: handle_skill_query(args))
                print(f"[Skills] {name} → {str(result).splitlines()[0][:120] if result else ''}")

            elif name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await loop.run_in_executor(None, undo_stack.undo_last)

            elif name == "open_app":
                r = await _computer(name, args)
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await _computer(name, args)
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "jarvis_window":
                window = str(args.get("window", "webview") or "webview").strip().lower().replace(" ", "_")
                action = str(args.get("action", "open") or "open").strip().lower()
                query = str(args.get("query", "") or "").strip()
                try:
                    # Widgets live on the Qt GUI thread — marshal the call there
                    # so the window actually opens (and animates) instead of
                    # silently doing nothing from this backend thread.
                    result = self.ui.call_on_ui(
                        lambda: self.ui.control_jarvis_window(window, action, query)
                    )
                except Exception as exc:
                    result = f"Could not control the JARVIS window: {exc}"

            elif name == "play_music":
                query = str(args.get("query", "") or "").strip()
                use_internal_webview = bool(
                    self.ui.feature_enabled("webview_engine")
                    and self.ui.feature_enabled("webview_music")
                )
                if not query:
                    result = "Please specify what music to play."
                elif use_internal_webview:
                    try:
                        result = self.ui.call_on_ui(lambda: self.ui.play_web_music(query))
                    except Exception as exc:
                        result = f"Music WebView error: {exc}"
                    # Internal player failed (codec/engine) — fall through to
                    # the YouTube action instead of reporting a dead end.
                    if isinstance(result, str) and result.strip().lower().startswith((
                        "music webview error", "internal webview",
                        "the embedded", "the internal", "webview music",
                    )):
                        try:
                            r = await loop.run_in_executor(
                                None, lambda: youtube_video(parameters={"action": "play", "query": query}, response=None, player=self.ui)
                            )
                            result = (r or "Done.") + " (played via the YouTube action — the internal WebView could not start it.)"
                        except Exception:
                            pass
                else:
                    # Explicitly fall back to the existing YouTube action when the
                    # user has disabled the internal WebView engine.
                    r = await loop.run_in_executor(
                        None, lambda: youtube_video(parameters={"action": "play", "query": query}, response=None, player=self.ui)
                    )
                    result = r or "Done."

            elif name == "youtube_video":
                # When the internal WebView engine is enabled, keep online video
                # inside the JARVIS HUD instead of launching the system browser.
                action = str(args.get("action", "play") or "play").strip().lower()
                use_internal_webview = bool(
                    getattr(getattr(self.ui, "_features", {}), "get", lambda *_: True)("webview_engine", True)
                )
                if action == "play" and use_internal_webview:
                    query = str(args.get("query", "") or "").strip()
                    url = str(args.get("url", "") or "").strip()
                    if not url:
                        if query.startswith(("http://", "https://")):
                            url = query
                        else:
                            from urllib.parse import quote_plus
                            url = ("https://www.youtube.com/results?search_query="
                                   + quote_plus(query or "YouTube"))
                    try:
                        self.ui.run_on_ui(lambda: self.ui._open_webview_panel(url))
                        result = "Opened the video/search inside JARVIS WebView."
                    except Exception as exc:
                        result = f"Could not open internal WebView: {exc}"
                else:
                    r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                    result = r or "Done."

            elif name == "play_local_video":
                try:
                    result = self.ui.call_on_ui(
                        lambda: self.ui.play_local_video(args.get("path") or None)
                    )
                except Exception as exc:
                    result = f"Local video error: {exc}"

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera" and not self.ui.feature_enabled("camera_access"):
                        self._vision_busy = False
                        result = "Camera access is revoked in Settings → Permissions, so I cannot open the webcam."
                    else:
                        if angle == "camera":
                            img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                            self.ui.start_camera_stream()
                            self._vision_cam_active = True
                            print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                            _stall = "camera"
                        else:
                            img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                            print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                            _stall = "screen"
                        self._pending_vision = (img_b, mime_t, user_text, angle)
                        try:
                            self.ui.run_on_ui(lambda: self.ui.scan_pulse())
                        except Exception:
                            pass
                        result = (
                            f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                            f"Immediately say ONE short natural sentence in English, "
                            f"telling them you are looking at their {_stall} right now. "
                            f"Do NOT describe or guess content — the actual image arrives in the NEXT message. "
                            f"When it arrives: if a person is visible, say how many people, what each is doing, "
                            f"what they are holding or wearing, and where they are in the frame. "
                            f"If it is a desk/room view, list the notable objects you see."
                        )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."
            elif name == "computer_settings":
                if not self.ui.feature_enabled("computer_control"):
                    result = "Computer control is disabled in Settings."
                else:
                    r = await _computer(name, args)
                    result = r or "Done."

            elif name == "desktop_control":
                if not self.ui.feature_enabled("computer_control"):
                    result = "Computer control is disabled in Settings."
                else:
                    r = await _computer(name, args)
                    result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen research panel. The UI also
                # extracts direct image URLs/markdown images and shows them in
                # the internal image preview when a search result contains one.
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "generate_image":
                _prompt = str(args.get("prompt") or "").strip()
                _ratio = str(args.get("aspect_ratio") or "16:9").strip() or "16:9"
                if not _prompt:
                    result = "No image prompt was provided."
                else:
                    def _gen_image_sync():
                        client = genai.Client(api_key=_get_api_key())
                        try:
                            cfg = types.GenerateContentConfig(
                                response_modalities=["IMAGE"],
                                image_config=types.ImageConfig(aspect_ratio=_ratio),
                            )
                        except Exception:
                            # Older/newer SDK variants can still accept the same
                            # structure as a plain mapping.
                            cfg = {
                                "response_modalities": ["IMAGE"],
                                "image_config": {"aspect_ratio": _ratio},
                            }
                        from core import model_router
                        response = model_router.generate(
                            client,
                            contents=_prompt,
                            role="image",
                            config=cfg,
                        )
                        out_dir = BASE_DIR / "generated_images"
                        out_dir.mkdir(parents=True, exist_ok=True)
                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        for part in getattr(response, "parts", []) or []:
                            if getattr(part, "inline_data", None):
                                image = part.as_image()
                                out_path = out_dir / f"jarvis_{stamp}.png"
                                image.save(out_path)
                                return str(out_path)
                        raise RuntimeError("The image model returned no image data.")

                    try:
                        out_path = await loop.run_in_executor(None, _gen_image_sync)
                        result = f"Generated image saved to {out_path}"
                        self.ui.show_image_path(out_path, "AI generated image")
                    except Exception as e:
                        result = f"Image generation failed: {e}"
                        self.ui.write_log(f"ERR: generate_image — {e}")
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                if not self.ui.feature_enabled("computer_control"):
                    result = "Computer control is disabled in Settings."
                else:
                    r = await _computer(name, args)
                    result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                _deferred.wait()  # get_system_status is bound by the loader
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    _release_vision_resources()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif name in ("vision_look", "recognize_faces", "what_am_i_holding",
                          "detect_objects", "detect_gesture"):
                # New subsystem tools — the coordinator (vision.manager) owns the
                # shared camera + pipeline and routes to mediapipe/local/AI backends.
                if not self.ui.feature_enabled("camera_access"):
                    result = "Camera access is revoked in Settings → Permissions, so I cannot use the webcam."
                else:
                    if name == "vision_look":
                        _q = str(args.get("question") or "").strip() or (
                            "Describe what you see in one or two short, natural sentences.")
                        result = await loop.run_in_executor(
                            None, lambda: _run_vision(self.ui, lambda vc: vc.describe_scene(_q)))
                    elif name == "recognize_faces":
                        result = await loop.run_in_executor(
                            None, lambda: _run_vision(self.ui, lambda vc: vc.who_is_there()))
                    elif name == "what_am_i_holding":
                        result = await loop.run_in_executor(
                            None, lambda: _run_vision(self.ui, lambda vc: vc.what_am_i_holding()))
                    elif name == "detect_objects":
                        result = await loop.run_in_executor(
                            None, lambda: _run_vision(self.ui, lambda vc: vc.detect_objects_now()))
                    elif name == "detect_gesture":
                        result = await loop.run_in_executor(
                            None, lambda: _run_vision(self.ui, lambda vc: vc.what_gesture()))
                    try:
                        self.ui.start_camera_stream()
                    except Exception:
                        pass

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"
                    _tool_ok = False

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            _tool_ok = False
            traceback.print_exc()
            self.speak_error(name, e)

        # Feed tool outcome into the learning engine (preferred methods for
        # future repeats). Additive; failures here never disturb the response.
        try:
            if not _tool_skip_learning:
                from core.brain_bridge import learn_tool_outcome
                learn_tool_outcome(name, _tool_ok, str(result)[:200])
        except Exception:
            pass

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {_corr} {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        """Forward queued realtime input (PC mic PCM, phone-relay PCM) to Gemini.

        Uses the SDK's dedicated `audio=` slot: passing a dict through the
        generic `media=` slot is accepted by the type hints but the live
        service ignores an audio payload without an explicit mime type, so the
        assistant never heard the microphone even though every send looked
        successful. The dict form {"data":..., "mime_type":"audio/pcm;rate=16000"}
        is a BlobDict — the SDK converts it.
        """
        while True:
            msg = await self.out_queue.get()
            try:
                await self.session.send_realtime_input(audio=msg)
            except Exception as exc:
                # One dropped chunk must never kill the sender task.
                print(f"[JARVIS] ⚠️ send_realtime_input failed: {exc}")
                await asyncio.sleep(0.05)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            # _last_cb exists before the stream starts (defined below, before
            # the `with`), so this is safe; the list avoids `nonlocal`.
            _last_cb[0] = time.monotonic()
            data = indata.tobytes()

            # HUD waveform: always reflect the real microphone — even while
            # JARVIS is speaking (that is exactly when you want to see that the
            # mic hears you for barge-in). Purely cosmetic; never disturbs audio.
            try:
                self.ui.set_audio_level(_pcm_level(indata))
            except Exception:
                pass

            # Local wake-word feed — observes the same PCM, never sends it
            # anywhere and never opens its own mic handle. On a detected wake
            # it barge-ins (stops JARVIS mid-speech) so the user is heard. It
            # must run even while JARVIS speaks, or barge-in could never fire.
            try:
                from core.brain_bridge import feed_wake_audio
                if feed_wake_audio(data):
                    self.ui.write_log("SYS: Wake word detected.")
            except Exception:
                pass

            # Send to the live session only when the user is actually being
            # listened to. Muted / push-to-talk off / phone mic active → the
            # session gets nothing, but the meter above still shows the level.
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if (
                not jarvis_speaking
                and not self.ui.muted
                and self.ui.voice_input_enabled
                and not self._phone_active
            ):
                def _enqueue(d=data):
                    try:
                        self.out_queue.put_nowait(
                            {"data": d, "mime_type": "audio/pcm;rate=16000"})
                    except Exception:
                        pass  # queue full → drop the frame, never raise in the loop
                loop.call_soon_threadsafe(_enqueue)

        try:
            def _open_mic(dev):
                return sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[JARVIS] 🎤 Input device: {_mic_name}")
            try:
                _mic_stream = _open_mic(_mic_dev)
            except Exception as _e:
                # A device the picker listed but the driver will not open right
                # now — exclusive mode, a webcam already in use, a virtual mic
                # whose source went away. Chosen hardware failing must never
                # mean the assistant cannot hear at all — and when NO mic exists
                # the session must keep running (text commands still work).
                if _mic_dev is None:
                    print(f"[JARVIS] ❌ No usable microphone: {_e} — voice input disabled.")
                    self.ui.write_log(
                        "SYS: No microphone found — voice input disabled, text commands work."
                    )
                    return
                print(f"[JARVIS] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                self.ui.write_log(
                    f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                )
                _mic_stream = _open_mic(None)

            last_cb = time.monotonic()
            # One-element list so the audio callback can update it without a
            # `nonlocal` chain across the two closures.
            _last_cb = [last_cb]

            with _mic_stream:
                print("[JARVIS] 🎤 Mic stream open")
                # Watchdog: a USB / virtual mic whose driver dies stops calling
                # the callback silently — the stream looks open but is deaf.
                # If no callback fires for 3 s, reopen the stream (same device,
                # then the default) instead of staying mute until restart.
                while True:
                    await asyncio.sleep(1.0)
                    if time.monotonic() - _last_cb[0] < 3.0:
                        continue
                    print("[JARVIS] ⚠️ Mic stream stalled — reopening…")
                    self.ui.write_log("SYS: Microphone stalled — reconnecting the mic.")
                    try:
                        _mic_stream.close()
                    except Exception:
                        pass
                    _reopened = False
                    for _dev in (_mic_dev, None):
                        try:
                            _mic_stream = _open_mic(_dev)
                            _reopened = True
                            break
                        except Exception as _re:
                            print(f"[JARVIS] ⚠️ Mic reopen failed ({_dev}): {_re}")
                    if _reopened:
                        _last_cb[0] = time.monotonic()
                        print("[JARVIS] 🎤 Mic stream reopened")
                    else:
                        await asyncio.sleep(2.0)
        except Exception as e:
            # Any runtime mic problem must never take the Live session down
            # with it (that would trigger an endless reconnect loop). Voice
            # input stops, text commands and all other ears keep working.
            print(f"[JARVIS] ❌ Mic: {e} — voice input disabled.")
            self.ui.write_log("SYS: Microphone error — voice input disabled, text commands work.")
            try:
                _mic_stream.close()
            except Exception:
                pass

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if not self._first_response_marked:
                        self._first_response_marked = True
                        _markup("AI_FIRST_RESPONSE")

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically. `resumable` goes false
                    # while a turn is mid-flight — replaying a handle from that
                    # moment is what the flag exists to prevent — so only
                    # resumable handles are kept. This is three lines and it is
                    # the entire fix for "every reconnect forgets everything".
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            if self._resume_handle is None:
                                print("[JARVIS] 🔗 Session resumption armed")
                            self._resume_handle = _sru.new_handle

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)
                                # gemini_piper: the reply TEXT is what Piper speaks,
                                # so it is streamed into the Piper worker as it
                                # arrives instead of waiting for the whole turn.
                                # The service keeps only the new suffix, so both
                                # delta-style and cumulative transcripts are safe.
                                if self._piper is not None:
                                    self._piper.feed(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        # Server-side barge-in (the user was heard mid-reply):
                        # stop Piper and forget what it still had queued, so a
                        # stale reply cannot resume after the interruption.
                        if getattr(sc, "interrupted", False) and self._piper is not None:
                            print("[JARVIS] ✋ Server interrupt — clearing Piper speech")
                            self._piper.interrupt()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                                # Feed the utterance into the Brain's observer
                                # (personality + correction learning). Never fatal.
                                try:
                                    from core.brain_bridge import observe_utterance
                                    observe_utterance(full_in)
                                except Exception:
                                    pass
                                # Live reference report: logs what "that"/"the
                                # other one" resolved to, so the user can see the
                                # assistant's understanding instead of guessing.
                                try:
                                    from core.brain_bridge import resolve_reference
                                    _ref = resolve_reference(full_in)
                                    if _ref.get("summary"):
                                        self._session_log.append(
                                            f"[context] {_ref['summary']}")
                                except Exception:
                                    pass
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                # JARVIS's own reply is context too: it names the
                                # file/website/object just handled, which is what
                                # "do the same thing" refers back to.
                                try:
                                    from core.brain_bridge import record_reply
                                    record_reply(full_out)
                                except Exception:
                                    pass
                                if self.voice_mode() == "local":
                                    threading.Thread(
                                        target=self.speak_local, args=(full_out,), daemon=True
                                    ).start()
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            # Turn finished: speak whatever Piper still holds
                            # (a fragment without a sentence terminator) and reset
                            # the per-turn transcript buffer.
                            if self._piper is not None:
                                self._piper.flush()
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until JARVIS finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        # ── Who renders the audible reply? Decide BEFORE touching the speaker.
        # local        → the selected local engine speaks at turn completion.
        # gemini_piper → Piper renders the reply from Gemini's transcript text,
        #                so Gemini's own audio is drained and discarded and the
        #                output stream is never even opened. That makes Gemini +
        #                Piper playing at once structurally impossible instead of
        #                a matter of ordering.
        # The queue is always consumed, so it can never grow without bound.
        _vmode = self.voice_mode()
        if _vmode in ("local", "gemini_piper"):
            _reason = ("Gemini native audio suppressed — Piper ONNX renders the reply"
                       if _vmode == "gemini_piper" else
                       "Voice output routed to the local engine (Settings ▸ Voice)")
            self.ui.write_log(f"SYS: {_reason}.")
            print(f"[JARVIS] 🔇 {_reason}")
            try:
                while True:
                    try:
                        await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        # Silence is normal between turns. Without this, the very
                        # first idle poll raised out of this task, which killed
                        # the audio task (and with it the session) — the task has
                        # to keep watching the queue forever.
                        continue
                    self._drain_audio_queue()
            except (asyncio.CancelledError, RuntimeError):
                pass
            return

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[JARVIS] 🔊 Output device: {_spk_name}")

        def _open_spk(dev):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=dev,
            )
            st.start()
            return st

        try:
            stream = _open_spk(_spk_dev)
        except Exception as _e:
            # A chosen output that the host API accepts by name but refuses to
            # open (exclusive mode, wrong sample rate, device asleep) must not
            # cost the user their voice. Fall back to the default and say so —
            # and if there is NO speaker at all, degrade to quiet mode instead
            # of killing the session (that would loop forever on reconnect).
            if _spk_dev is None:
                print(f"[JARVIS] ❌ No usable speaker: {_e} — audio output disabled.")
                self.ui.write_log(
                    "SYS: No speaker found — silent mode, text stays on screen."
                )
                return
            print(f"[JARVIS] ⚠️  Output device '{_spk_name}' failed: {_e} — using default")
            self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            try:
                stream = _open_spk(None)
            except Exception as _e2:
                print(f"[JARVIS] ❌ No usable speaker: {_e2} — audio output disabled.")
                self.ui.write_log("SYS: No speaker found — silent mode, text stays on screen.")
                return

        # Gemini-native modes only reach this point, so everything below plays
        # the realtime voice exactly as before.
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)

                if not self._marked_first_audio:
                    self._marked_first_audio = True
                    _markup("FIRST_AUDIO")

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform from JARVIS's own voice while speaking.
                try:
                    self.ui.set_audio_level(_pcm_level(
                        np.frombuffer(bytes(batch), dtype=np.int16)))
                except Exception:
                    pass

                try:
                    await asyncio.to_thread(stream.write, bytes(batch))
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception:
            # Runtime playback error must not take the Live session down (which
            # would loop forever reconnecting). Audio stops; UI text remains.
            pass
        finally:
            self.set_speaking(False)
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """Speak one concise time-aware greeting when a session starts."""
        hour = datetime.now().hour
        # Address the user by name when configured; vary the phrasing so the
        # assistant never sounds like a broken record at every boot.
        try:
            _uname = (_read_cached_json(API_CONFIG_PATH) or {}).get("user_name", "") or ""
        except Exception:
            _uname = ""
        addr = f"{_uname.strip()}" if _uname.strip() else "sir"
        if 5 <= hour < 12:
            opts = [f"Good morning, {addr}. All systems are online — how may I assist?",
                    f"Good morning, {addr}. Awaits your instruction.",
                    f"Morning, {addr}. Everything is running smoothly. What can I do for you?"]
        elif 12 <= hour < 18:
            opts = [f"Good afternoon, {addr}. How may I be of service?",
                    f"Afternoon, {addr}. All systems nominal — at your command.",
                    f"Good afternoon, {addr}. What shall we work on?"]
        elif 18 <= hour < 22:
            opts = [f"Good evening, {addr}. How may I assist you this evening?",
                    f"Evening, {addr}. Standing by for your instructions.",
                    f"Good evening, {addr}. At your service, as always."]
        else:
            opts = [f"Working late, {addr}? I'm at your disposal.",
                    f"Good night, {addr}. How may I assist you?",
                    f"Still up, {addr}? I remain at your service."]
        greeting = random.choice(opts)

        # Small yield so the greeting does not race the session's first receive
        # cycle — not a fixed "greeting delay"; it adds nothing measurable and
        # the session may legitimately vanish in between.
        await asyncio.sleep(0.05)
        if not self.session:
            return
        # Local-voice mode: the greeting was already spoken through the local
        # engine by prewarm_voice; only show it in the log here. gemini_piper is
        # NOT skipped — the greeting goes through Gemini and Piper speaks it.
        if self.voice_mode() == "local":
            self.ui.write_log(f"{self._asst_name}: {greeting}")
            _markup("GREETING_SENT")
            _markup("STARTUP_COMPLETE")
            return
        try:
            await self.session.send_client_content(
                turns={"parts": [{
                    "text": f'Say exactly: "{greeting}" Do not add anything else and do not call any tools.'
                }]},
                turn_complete=True,
            )
        except Exception as e:
            print(f"[JARVIS] ⚠️ Greeting failed: {e}")
            return
        self.ui.write_log("SYS: Startup greeting sent.")
        _markup("GREETING_SENT")
        _markup("STARTUP_COMPLETE")
        return

        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting (always English) ──────────────────────
        lang_clause = " Speak this greeting in English."
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = " Speak in English."

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        # "Keep chat history" is off: drop the log instead of writing a session
        # summary. The turns are already in the conversation ledger for the
        # current run, and nothing about this session is persisted.
        try:
            if not bool((_read_cached_json(API_CONFIG_PATH) or {})
                        .get("features", {}).get("chat_history", True)):
                self._session_log = []
                return
        except Exception:
            pass
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import model_router
            from google import genai as _genai
            client = _genai.Client(api_key=_get_api_key())
            resp   = await asyncio.to_thread(
                model_router.generate,
                client,
                contents=prompt,
                role="fast",
            )
            summary = (resp.text or "").strip()
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds.

        Fully disabled unless features['system_alerts'] is true — when off,
        no hardware warning is ever spoken or injected.
        """
        while True:
            await asyncio.sleep(10)
            try:
                _feat = (_read_cached_json(API_CONFIG_PATH) or {}).get("features", {})
                if not bool((_feat or {}).get("system_alerts", False)):
                    continue
            except Exception:
                continue
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            self.ui.write_log(f"SYS: Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_memory_maintenance(self) -> None:
        """Periodic brain-memory upkeep (spec §6): consolidate duplicates,
        decay stale low-confidence facts, purge expired context. Runs in the
        background task group so it can never block the AI session; every
        failure is contained. Interval: 30 min after a 10-min first pass."""
        await asyncio.sleep(600)
        while True:
            try:
                await asyncio.to_thread(self._memory_maintenance_pass)
            except Exception as e:
                print(f"[Memory] maintenance skipped: {e}")
            await asyncio.sleep(1800)

    def _memory_maintenance_pass(self) -> dict:
        try:
            from memory.manager import get_brain_memory
            brain = get_brain_memory()
            if brain is None:
                return {}
            brain.decay()
            stats = brain.consolidate()
            print(f"[Memory] consolidated: {stats}")
            return stats
        except Exception:
            return {}

    async def _run_proactive_mode(self) -> None:
        """
        Background task: speaks unprompted ONLY when the user enabled
        features['proactive_speaking']. Default off — JARVIS stays silent
        until spoken to.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            try:
                _feat = (_read_cached_json(API_CONFIG_PATH) or {}).get("features", {})
                if not bool((_feat or {}).get("proactive_speaking", False)):
                    continue
            except Exception:
                continue

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    def _ensure_dashboard(self) -> None:
        """Build + serve the dashboard off the AI critical path.

        One retry after 30 s, then it stays off — this mirrors the previous
        behavior for a missing/broken dashboard while guaranteeing no busy
        loop and no interference with reconnects (spec §20/§AI-RECONNECT).
        """
        for attempt in (1, 2):
            try:
                from dashboard.server import DashboardServer
                dashboard = DashboardServer()
                dashboard.set_connect_callback(self._on_phone_connected)
                self._dashboard = dashboard
                self._dashboard_state = "ready"
                self.ui.write_log("SYS: Remote Dashboard ready.")
                _loop = self._loop
                if _loop is not None:
                    try:
                        _loop.call_soon_threadsafe(
                            lambda: _loop.create_task(dashboard.serve())
                        )
                        _loop.call_soon_threadsafe(
                            lambda: _loop.create_task(self._process_dashboard_commands())
                        )
                    except RuntimeError:
                        pass  # loop gone during shutdown
                return
            except Exception as e:
                if attempt == 1:
                    time.sleep(30)
                else:
                    self._dashboard_state = "off"
                    print(f"[Dashboard] Disabled: {e}")

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._reconnect_event = asyncio.Event()
        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
        )
        set_trim_notifier(self.ui.write_log)

        # Wake-word barge-in: a detected wake word calls the same interrupt path
        # the voice UI uses, so JARVIS stops mid-speech and listens (spec §23).
        # Only wired up when the user left barge-in and voice interruption on —
        # otherwise the hook would stop speech it was told not to interrupt.
        try:
            _bfeats = (_read_cached_json(API_CONFIG_PATH) or {}).get("features", {}) or {}
            if bool(_bfeats.get("barge_in", True)) and bool(_bfeats.get("voice_interruption", True)):
                from core.brain_bridge import set_wake_barge_in
                set_wake_barge_in(lambda: self.interrupt(source="voice"))
        except Exception:
            pass

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        _markup("AUDIO_INIT_BEGIN")
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.
        audio_devices.prefetch()
        _markup("AUDIO_INIT_DONE")

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography).
        # Boot profiling: importing dashboard.server cost ~2.8 s here (FastAPI,
        # uvicorn, cryptography) and it sat on the critical path BEFORE the
        # Gemini connect. It is pure remote-control sugar with zero role in AI
        # readiness, so it now boots on a background thread AFTER the AI is
        # connecting; _ensure_dashboard() swaps it in once live.
        self._dashboard_state = "building"
        threading.Thread(target=self._ensure_dashboard, name="jarvis-dashboard-boot", daemon=True).start()

        while True:
            try:
                print("[JARVIS] Connecting...")
                # Honest AI ready-state: the UI must not claim READY before the
                # Live session is genuinely usable, nor sit frozen at STARTING.
                self.ui.set_state(
                    "RECONNECTING" if getattr(self, "_conn_backoff", 0) or self._resume_handle
                    else "CONNECTING"
                )
                _resumed_with = self._resume_handle is not None
                _markup("AI_CONFIG_LOAD")
                config = self._build_config()

                # Reuse one authenticated client across reconnects. It is a thin
                # handle with no sockets — recreating it per attempt wasted work
                # and forced re-reading the API key from disk every retry.
                _version = "v1alpha" if self._enhanced_live else "v1beta"
                _key     = _get_api_key()
                if (self._client is not None
                        and self._client_key == _key
                        and self._client_version == _version):
                    client = self._client
                else:
                    _markup("AI_CLIENT_BEGIN")
                    # The deferred loader may already have prewarmed this exact
                    # client in the background; build under the same lock it
                    # uses, so only one side ever constructs it.
                    with self._client_lock:
                        if self._client is not None:
                            # Prewarm landed while _build_config ran — use it.
                            client = self._client
                        else:
                            client = genai.Client(
                                api_key=_key,
                                http_options={"api_version": _version},
                            )
                            self._client         = client
                            self._client_key     = _key
                            self._client_version = _version
                    _markup("AI_CLIENT_DONE")

                try:
                    from core import ai_providers as _aip
                    _acfg = _read_cached_json(API_CONFIG_PATH)
                    _live_model = str(_acfg.get("ai_model") or LIVE_MODEL).strip() or LIVE_MODEL
                except Exception:
                    _live_model = LIVE_MODEL
                _markup("GEMINI_CONNECT_BEGIN")
                async with (
                    client.aio.live.connect(model=_live_model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[JARVIS] Connected.")
                    _markup("GEMINI_CONNECTED")
                    _markup("GEMINI_SESSION_READY")
                    if _resumed_with:
                        # Say it plainly: the difference between "it reconnected"
                        # and "it reconnected and still knows what we were doing"
                        # is the whole point, and it is invisible otherwise.
                        self.ui.write_log("SYS: Reconnected — conversation restored.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")
                    _log_vision_status()

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    # SAFE MODE (spec §53-§54): after repeated crashes the
                    # optional background work stays off so the assistant can
                    # still be talked to while whatever was failing is repaired.
                    # Talking, memory and tools all keep working.
                    _safe = False
                    try:
                        from core.diagnostics import safe_mode as _safe_mode
                        _safe = bool(_safe_mode())
                    except Exception:
                        _safe = False

                    self._reconnect_event.clear()  # ignore requests from before this session
                    tg.create_task(self._watch_reconnect())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    if _safe:
                        self.ui.write_log(
                            "SYS: SAFE MODE — background monitoring, proactive "
                            "checks and memory maintenance are off (say \"run "
                            "diagnostics\" or clear safe mode in Settings).")
                    else:
                        tg.create_task(self._run_system_monitor())
                        tg.create_task(self._run_background_monitor())
                        tg.create_task(self._run_proactive_mode())
                        tg.create_task(self._run_memory_maintenance())
                        if self._dashboard:
                            tg.create_task(self._relay_phone_audio())
                    _markup("AUDIO_TASKS_STARTED")
                    # A clean connect clears the crash streak (spec §53).
                    try:
                        from core.diagnostics import note_startup_ok
                        note_startup_ok()
                    except Exception:
                        pass

                    # Startup greeting fires once per process launch.
                    # The voice engine prewarms in parallel so the greeting has
                    # zero TTS engine-init delay when its reply audio arrives.
                    if not self._briefing_sent:
                        self._briefing_sent = True
                        self.prewarm_voice()
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Voluntary reconnect (voice change) — not an error. Rebuild the
                # session immediately with no backoff and no scary logs.
                if _is_reconnect_signal(e):
                    print("[JARVIS] Voluntary reconnect requested.")
                    if not _keep_context_of(e):
                        # A deliberate clean slate (voice change) — drop the
                        # handle so the next connect really does start empty.
                        self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                # A resumption handle the server will not accept — expired, or
                # belonging to a session it has since dropped. Without this, the
                # same dead handle would be replayed on every retry and the
                # assistant would never come back at all: the feature meant to
                # survive a reconnect would be the thing preventing one. Drop it
                # once and let the next attempt start clean.
                if _resumed_with and (
                    "resum" in str(e).lower()
                    or "handle" in str(e).lower()
                    or "INVALID_ARGUMENT" in str(e)
                    or "NOT_FOUND" in str(e)
                ):
                    print("[JARVIS] 🔗 Resumption handle rejected — starting a fresh session")
                    self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                    self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Crash bookkeeping (spec §53): three failures inside ten
                # minutes is a loop, not bad luck — that enters safe mode.
                try:
                    from core.diagnostics import note_crash
                    if note_crash(f"{type(e).__name__}: {err_str[:200]}"):
                        self.ui.write_log(
                            "SYS: Three failures in ten minutes — entering SAFE "
                            "MODE. Background work is paused until you clear it.")
                except Exception:
                    pass

                # Enhanced audio features rejected by the server (preview API
                # drift) — drop them and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "affective" in err_str.lower()
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Advanced audio features unavailable — reconnecting without them."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    self._conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.set_state("RECONNECTING")
                    self.ui.write_log(
                        f"NET: Connection failed — retrying in {_conn_backoff}s."
                        " (A VPN may be required.)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                # Only save if there was a real conversation (≥3 turns)
                if len(self._session_log) >= 3:
                    asyncio.create_task(self._save_session_summary())

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def _load_backend_modules():
    """Load the AI-CRITICAL backend dependencies after the UI is visible.

    Boot profiling showed the full serial import tail cost 10-14 s while the
    Gemini connect itself needs only a small core: audio I/O, the genai SDK,
    memory, config, plugins and the confirm/undo gates. Everything else (the
    command-action modules: open_app, weather, youtube, browser, ...) loads
    concurrently with the network connect via _DeferredModules, and the tool
    dispatcher barriers on it with _deferred.wait(). A bad/missing backend
    dependency still cannot make main.py flash and disappear.
    """
    global sd, np, genai, types
    global load_memory, update_memory, format_memory_for_prompt
    global save_session_summary, pop_last_session, search_memory, set_trim_notifier
    global file_processor, flight_finder, open_app, weather_action, send_message, reminder
    global computer_settings, _capture_camera, _capture_screen, youtube_video
    global desktop_control, browser_control, file_controller, code_helper, dev_agent
    global web_search_action, computer_control, game_updater, SystemMonitor, get_system_status
    global ProactiveEngine, add_monitor, remove_monitor, list_monitors, monitor_check_all
    global _fetch_news_sync, get_voice, get_input_device, get_output_device
    global discover_plugins, undo_stack, confirm_gate, audio_devices

    import sounddevice as _sd
    import numpy as _np
    from google import genai as _genai
    from google.genai import types as _types
    sd, np, genai, types = _sd, _np, _genai, _types

    from memory.memory_manager import (
        load_memory as _load_memory, update_memory as _update_memory,
        format_memory_for_prompt as _format_memory_for_prompt,
        save_session_summary as _save_session_summary, pop_last_session as _pop_last_session,
        search_memory as _search_memory, set_trim_notifier as _set_trim_notifier,
    )
    load_memory, update_memory = _load_memory, _update_memory
    format_memory_for_prompt, save_session_summary = _format_memory_for_prompt, _save_session_summary
    pop_last_session, search_memory, set_trim_notifier = _pop_last_session, _search_memory, _set_trim_notifier

    from memory.config_manager import get_voice, get_input_device, get_output_device
    from core.plugin_loader import discover_plugins
    from core import undo as undo_stack
    from core import confirm as confirm_gate
    from core import audio_devices


def _load_action_modules():
    """Bind the command-action globals used by the tool dispatcher.

    Called from _DeferredModules after its background __import__ pass, so by
    the time this runs every module is already in sys.modules and these are
    pure attribute bindings (microseconds).
    """
    global file_processor, flight_finder, open_app, weather_action, send_message, reminder
    global computer_settings, _capture_camera, _capture_screen, youtube_video
    global desktop_control, browser_control, file_controller, code_helper, dev_agent
    global web_search_action, computer_control, game_updater, SystemMonitor, get_system_status
    global ProactiveEngine, add_monitor, remove_monitor, list_monitors, monitor_check_all
    global _fetch_news_sync

    from actions.file_processor import file_processor
    from actions.flight_finder import flight_finder
    from actions.open_app import open_app
    from actions.weather_report import weather_action
    from actions.send_message import send_message
    from actions.reminder import reminder
    from actions.computer_settings import computer_settings
    from actions.screen_processor import _capture_camera, _capture_screen
    from actions.youtube_video import youtube_video
    from actions.desktop import desktop_control
    from actions.browser_control import browser_control
    from actions.file_controller import file_controller
    from actions.code_helper import code_helper
    from actions.dev_agent import dev_agent
    from actions.web_search import web_search as web_search_action, _news as _fetch_news_sync
    from actions.computer_control import computer_control
    from actions.game_updater import game_updater
    from actions.system_monitor import SystemMonitor, get_system_status
    from actions.proactive import ProactiveEngine
    from actions.background_monitor import add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all


def main():
    # Import the UI lazily so we can display a useful error instead of
    # silently terminating when a Qt dependency is broken.
    _markup("PROCESS")
    error_log = BASE_DIR / "jarvis_startup_error.log"
    try:
        from ui import JarvisUI
        _markup("UI_IMPORT")
        ui_path = BASE_DIR / "face.png"
        ui = JarvisUI(str(ui_path))
        _markup("UI_CREATED")
    except Exception:
        text = traceback.format_exc()
        try:
            error_log.write_text(text, encoding="utf-8")
        except Exception:
            pass
        # Never silently disappear when launched from Explorer/VS Code.
        # Show the exact error using a native Windows dialog when possible.
        try:
            if _platform.system() == "Windows":
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "J.A.R.V.I.S could not start.\n\n"
                    + text[-3500:]
                    + "\n\nThe full traceback was saved to:\n"
                    + str(error_log),
                    "J.A.R.V.I.S Startup Error",
                    0x10,
                )
        except Exception:
            pass
        return

    def runner():
        try:
            # The UI is already running before the heavy backend imports. The
            # loader runs on its own daemon thread: phase 1 loads the AI-critical
            # core (waited on below), phase 2 keeps importing the command-action
            # tail CONCURRENTLY with the Gemini network connect.
            _markup("BACKEND_IMPORTS_BEGIN")
            _deferred.set_owner(None)
            _deferred.start()
            _deferred.wait_critical()
            _markup("BACKEND_IMPORTS_DONE")
            _markup("CONFIG_LOAD_BEGIN")
            ui.wait_for_api_key()
            _markup("CONFIG_LOAD_DONE")
            _markup("JCORE_CONSTRUCT_BEGIN")
            _deferred.set_owner(None)   # constructed client not yet reusable
            jarvis = JarvisLive(ui)
            _deferred.set_owner(jarvis)  # phase 3 can now prewarm the client
            _markup("JCORE_CONSTRUCT_DONE")
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
            _release_vision_resources()
            try:
                from core.hybrid_voice import HybridVoiceSystem, PiperSpeechService
                HybridVoiceSystem.reset()
                PiperSpeechService.reset()   # stop/join the Piper worker thread
            except Exception:
                pass
        except Exception:
            text = traceback.format_exc()
            try:
                error_log.write_text(text, encoding="utf-8")
            except Exception:
                pass
            ui.write_log("ERR: Backend startup failed — see jarvis_startup_error.log")
            ui.set_state("ERROR")
        finally:
            try:
                from core.hybrid_voice import HybridVoiceSystem, PiperSpeechService
                HybridVoiceSystem.reset()
                PiperSpeechService.reset()   # stop/join the Piper worker thread
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True, name="jarvis-backend").start()
    _markup("BACKEND_THREAD_STARTED")

    # Optional boot benchmark: when JARVIS_BOOT_TIMEOUT_MS is set, quit the
    # event loop after that many milliseconds so a profiling run (or CI boot
    # smoke test) ends cleanly without killing the process. No-op when unset.
    _boot_timeout_ms = int(os.environ.get("JARVIS_BOOT_TIMEOUT_MS") or "0")
    if _boot_timeout_ms > 0:
        try:
            from PyQt6.QtCore import QTimer
            from PyQt6.QtWidgets import QApplication as _QApp
            _app = _QApp.instance()
            if _app is not None:
                QTimer.singleShot(max(1000, int(_boot_timeout_ms)), _app.quit)
        except Exception:
            pass
    ui.root.mainloop()

if __name__ == "__main__":
    main()