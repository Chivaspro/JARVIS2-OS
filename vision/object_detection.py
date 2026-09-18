"""Real-time local object detection with pluggable backends.

Backends
--------
``ultralytics`` (YOLO)   — best accuracy; requires ``pip install ultralytics``
                           and a ``.pt`` / ``.onnx`` model.
``mediapipe``            — EfficientDet TFLite task file in the model dir
                           (Apache-2.0); downloads the default model if absent
                           and a network is reachable.
``opencv``               — YOLOv4/.onnx via ``cv2.dnn``; needs weights files.
``ai``                   — no local model; detection is delegated to the
                           multimodal provider (slow, but always available).
``off``                  — detection disabled.

Backend selection is ``auto`` by default: the first backend for which a
model file is actually present wins.  When nothing local is installed the
detector transparently routes to the multimodal provider so JARVIS can
still answer "what do you see?" — via the exact same public API.

Every method is defensive: a broken model returns an empty list / a
friendly error message and never propagates an exception to the caller.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path
from typing import List, Optional

from vision.config import VisionConfig
from vision.models import Detection, Rect
from vision.utils import frame_to_png_b64, is_package_available, logger

try:
    import numpy as np
    _NP = True
except Exception:  # pragma: no cover
    np = None  # type: ignore
    _NP = False

try:
    import cv2
    _CV2 = True
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore
    _CV2 = False


# Default MediaPipe ObjectDetector (EfficientDet-Lite0, 4.6 MB, Apache-2.0).
_DEFAULT_TFLITE_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "object_detector/efficientdet_lite0/float32/latest/efficientdet_lite0.tflite"
)
_DEFAULT_TFLITE_NAME = "efficientdet_lite0.tflite"


class ObjectDetectionError(RuntimeError):
    pass


class ObjectDetector:
    """Detector facade. Create via :meth:`create` and call ``detect()``."""

    def __init__(self, cfg: VisionConfig):
        self._cfg = cfg
        self._backend = ""
        self._model = None        # mediapipe task / ultralytics model / None
        self._names = []          # class-name list (mediapipe/opencv)
        self._ai_provider = None  # injected by the coordinator for 'ai' backend
        self._ai_provider_getter = None
        self._device = cfg.inference_device

        self._tracker = None
        self._tracker_inited = False
        self._prev_boxes: list = []

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def create(cls, cfg: VisionConfig,
               ai_provider_getter=None) -> "ObjectDetector":
        detector = cls(cfg)
        detector._ai_provider_getter = ai_provider_getter
        detector.ensure_backend()
        return detector

    def ensure_backend(self) -> str:
        """Pick and load the best available backend. Returns backend name."""
        cfg = self._cfg
        requested = (cfg.object_detector_backend or "auto").strip().lower()
        if requested == "off":
            self._backend = ""
            return ""
        if requested == "ai":
            self._backend = "ai"
            return "ai"

        candidates = []
        if requested == "auto":
            candidates = ["ultralytics", "mediapipe", "opencv", "ai"]
        elif requested == "ultralytics":
            candidates = ["ultralytics", "ai"]
        elif requested == "mediapipe":
            candidates = ["mediapipe", "ai"]
        elif requested == "opencv":
            candidates = ["opencv", "ai"]
        else:
            candidates = ["ai"]

        for name in candidates:
            try:
                if name == "ultralytics" and self._try_load_ultralytics():
                    self._backend = "ultralytics"
                    return self._backend
                if name == "mediapipe" and self._try_load_mediapipe():
                    self._backend = "mediapipe"
                    return self._backend
                if name == "opencv" and self._try_load_opencv():
                    self._backend = "opencv"
                    return self._backend
            except Exception as exc:
                logger.warn(f"backend {name} failed to load: {exc} continue")

        if requested != "off":
            self._backend = "ai"
        return self._backend

    @property
    def backend(self) -> str:
        return self._backend

    def ready(self) -> bool:
        return self._backend in ("ultralytics", "mediapipe", "opencv")

    # ── model loading helpers ───────────────────────────────────────────────

    def _model_path(self, *names: str) -> Path:
        for name in names:
            p = self._cfg.object_model_path / name
            if p.exists():
                return p
        return Path(self._cfg.object_model_path) / (names[0] if names else "")

    def _try_load_ultralytics(self) -> bool:
        if not is_package_available("ultralytics"):
            return False
        model_file = self._model_path("yolov8n.pt", "yolov8n.onnx",
                                      "yolo11n.pt", "best.pt")
        if not model_file.exists():
            logger.info("ultralytics installed but no model file found "
                        f"(looked in {self._cfg.object_model_path})")
            return False
        from ultralytics import YOLO
        self._model = YOLO(str(model_file))
        self._names = list((self._model.names or {}).values())
        logger.info("object detection backend: ultralytics "
                    f"(device={self._device})")
        return True

    def _try_load_mediapipe(self) -> bool:
        if not is_package_available("mediapipe"):
            return False
        model_file = self._model_path(_DEFAULT_TFLITE_NAME, "efficientdet.tflite",
                                      "best.tflite")
        if not model_file.exists():
            logger.info("mediapipe installed but no object model present")
            downloaded = self._download_default_tflite(model_file)
            if not downloaded:
                return False
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
            base = mp_python.BaseOptions
            opts = mp_vision.ObjectDetectorOptions(
                base_options=base(model_asset_path=str(model_file)),
                running_mode=mp_vision.RunningMode.IMAGE,
                score_threshold=max(0.05, min(0.9, self._cfg.confidence_threshold)),
                max_results=40,
            )
            self._model = mp_vision.ObjectDetector.create_from_options(opts)
            self._names = ([""] * 91)  # COCO 91 classes; labels come from results
            logger.info("object detection backend: mediapipe-tflite")
            return True
        except Exception as exc:
            logger.warn(f"mediapipe object model failed to load: {exc}")
            self._model = None
            return False

    def _download_default_tflite(self, out: Path) -> bool:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"downloading efficientdet_lite0 → {out}")
            with urllib.request.urlopen(_DEFAULT_TFLITE_URL, timeout=60) as r, \
                    open(out, "wb") as f:
                shutil.copyfileobj(r, f)
            logger.info("model downloaded")
            return True
        except Exception as exc:
            logger.warn(f"model download failed: {exc}")
            return False

    def _try_load_opencv(self) -> bool:
        if not _CV2:
            return False
        model_dir = self._cfg.object_model_path
        weights = self._model_path("yolov4-tiny.weights", "yolov4.weights",
                                   "yolov3.weights")
        cfg_file = self._model_path("yolov4-tiny.cfg", "yolov4.cfg", "yolov3.cfg")
        if not weights.exists() or not cfg_file.exists():
            logger.info("opencv backend selected but weights/cfg missing")
            return False
        net = cv2.dnn.readNet(str(weights), str(cfg_file))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        if self._device == "cuda":
            try:
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
            except Exception:
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._model = net
        try:
            self._names = [x.strip() for x in
                           (model_dir / "coco.names").read_text().splitlines()]
        except Exception:
            self._names = []
        logger.info("object detection backend: opencv-dnn")
        return True

    # ── detection ───────────────────────────────────────────────────────────

    def detect(self, frame_bgr) -> List[Detection]:
        """Detect objects in a BGR frame. Robust: never raises."""
        try:
            if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
                return []
            if not self.ready():
                return self._detect_via_ai(frame_bgr)
            if self._backend == "ultralytics":
                return self._detect_ultralytics(frame_bgr)
            if self._backend == "mediapipe":
                return self._detect_mediapipe(frame_bgr)
            if self._backend == "opencv":
                return self._detect_opencv(frame_bgr)
            return self._detect_via_ai(frame_bgr)
        except ObjectDetectionError:
            raise
        except Exception as exc:
            logger.error(f"object detection failed: {exc}")
            return []

    def _detect_ultralytics(self, frame_bgr) -> List[Detection]:
        results = self._model.predict(frame_bgr, conf=self._cfg.confidence_threshold,
                                      verbose=False, device=None)
        if not results:
            return []
        boxes = results[0].boxes
        if boxes is None:
            return []
        height, width = frame_bgr.shape[:2]
        dets: List[Detection] = []
        for box in boxes:
            try:
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                conf = float(box.conf[0])
                cls = int(box.cls[0])
                label = str(self._names[cls]) if 0 <= cls < len(self._names) else f"class_{cls}"
                dets.append(Detection(
                    label=label, confidence=conf,
                    rect=Rect(x=max(0.0, x1 / width), y=max(0.0, y1 / height),
                              w=min(1.0, max(0.0, (x2 - x1) / width)),
                              h=min(1.0, max(0.0, (y2 - y1) / height))),
                    class_id=cls, source="ultralytics",
                ))
            except Exception:
                continue
        return self._associate_tracks(dets)

    def _detect_mediapipe(self, frame_bgr) -> List[Detection]:
        import cv2
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        import numpy as np
        mp_image = None
        try:
            from mediapipe.tasks.python import vision as mp_vision
            mp_image = mp_vision.Image(image_format=mp_vision.ImageFormat.SRGB,
                                       data=rgb)
        except Exception:
            mp_image = None
        result = self._model.detect(mp_image)
        dets: List[Detection] = []
        for det in (result.detections or []):
            try:
                bb = det.bounding_box
                w, h = frame_bgr.shape[1], frame_bgr.shape[0]
                x, y = float(bb.origin_x) / w, float(bb.origin_y) / h
                bw, bh = float(bb.width) / w, float(bb.height) / h
                cats = det.categories or []
                label = cats[0].category_name if cats else "object"
                conf = float(cats[0].score) if cats else 0.0
                dets.append(Detection(label=label, confidence=conf,
                                      rect=Rect(x=x, y=y, w=bw, h=bh),
                                      class_id=cats[0].index if cats else -1,
                                      source="mediapipe"))
            except Exception:
                continue
        return self._associate_tracks(dets)

    def _detect_opencv(self, frame_bgr) -> List[Detection]:
        h, w = frame_bgr.shape[:2]
        blob = cv2.dnn.blobFromImage(frame_bgr, 1 / 255.0, (416, 416),
                                     swapRB=True, crop=False)
        self._model.setInput(blob)
        outs = self._model.forward(self._model.getUnconnectedOutLayersNames())
        dets: List[Detection] = []
        conf_thresh = self._cfg.confidence_threshold
        for out in outs:
            for row in out:
                scores = row[5:]
                cls = int(np.argmax(scores))
                conf = float(scores[cls])
                if conf < conf_thresh:
                    continue
                cx, cy, bw, bh = [float(v) for v in row[:4]]
                x1 = (cx - bw / 2) / w
                y1 = (cy - bh / 2) / h
                label = self._names[cls] if 0 <= cls < len(self._names) else f"class_{cls}"
                dets.append(Detection(label=label, confidence=conf,
                                      rect=Rect(x=max(0.0, x1), y=max(0.0, y1),
                                                w=min(1.0, max(0.0, bw / w)),
                                                h=min(1.0, max(0.0, bh / h))),
                                      class_id=cls, source="opencv"))
        return self._associate_tracks(dets)

    def _detect_via_ai(self, frame_bgr) -> List[Detection]:
        """Multimodal fallback: ask the vision model for rough bounding boxes.

        This path has no strict real-time guarantees and is throttled by the
        coordinator — the output is expressed as approximate object names +
        locations so JARVIS can still count / describe without YOLO.
        """
        getter = self._ai_provider_getter
        if getter is None:
            return []
        provider = getter()
        if provider is None or not provider.is_available():
            return []
        question = (
            "List the visible objects as compact lines, one per object, "
            "formatted as: Name | approximate-left-tenth | top-tenth. "
            "Use only the tenths 1-9 for position. Be brief."
        )
        try:
            text = provider.analyze_image(frame_bgr, question)
        except Exception as exc:
            logger.warn(f"AI detection failed: {exc}")
            return []
        return _parse_ai_detections(text)

    # ── tracking (cheap centroid IoU association) ───────────────────────────

    def _associate_tracks(self, dets: List[Detection]) -> List[Detection]:
        if not self._cfg.object_tracking or not dets:
            self._prev_boxes = []
            return dets
        from vision.utils import iou
        new_dets = []
        used_prev: set = set()
        next_id = (max([d.tracker_id for d in dets] or [0]) + 1 if dets else 0)
        for d in dets:
            best_iou, best_i = -1.0, -1
            for i, (pbox, pid) in enumerate(self._prev_boxes):
                if i in used_prev:
                    continue
                if pbox.label != d.label:
                    continue
                v = iou((pbox.rect.x, pbox.rect.y, pbox.rect.w, pbox.rect.h),
                        (d.rect.x, d.rect.y, d.rect.w, d.rect.h))
                if v > best_iou:
                    best_iou, best_i = v, i
            if best_iou >= 0.35:
                d.tracker_id = self._prev_boxes[best_i][1]
                used_prev.add(best_i)
            else:
                d.tracker_id = next_id
                next_id += 1
            new_dets.append(d)
        self._prev_boxes = [(d, d.tracker_id) for d in new_dets]
        return new_dets

    # ── health ──────────────────────────────────────────────────────────────

    def describe(self) -> str:
        if not self.ready():
            return f"backend={self._backend} (AI fallback)"
        return f"backend={self._backend} device={self._device}"


# ── parser for the AI fallback text ────────────────────────────────────────

def _parse_ai_detections(text: str) -> List[Detection]:
    """Parse 'Name | left-tenth | top-tenth' lines the multimodal model emits."""
    dets: List[Detection] = []
    if not text:
        return dets
    for raw_line in (text or "").splitlines():
        line = raw_line.strip("- ").strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        try:
            fx, fy = int(parts[1]), int(parts[2])
            fx = max(1, min(9, fx)); fy = max(1, min(9, fy))
        except Exception:
            continue
        dets.append(Detection(
            label=parts[0][:32],
            confidence=0.5,
            rect=Rect(x=(fx - 0.5) / 10.0, y=(fy - 0.5) / 10.0,
                      w=0.10, h=0.10),
            source="ai",
        ))
    return dets


__all__ = ["ObjectDetector", "ObjectDetectionError"]