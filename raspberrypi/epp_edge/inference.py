"""Hilo de inferencia: toma siempre el frame más reciente y nunca acumula atraso."""
import hashlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .config import Settings
from .models import Detection, Frame, Inference
from .slots import LatestSlot


def compute_model_version(model_path: Path) -> str:
    """Primeros 12 caracteres del SHA-256 del modelo. En NCNN (directorio) se usa model.ncnn.bin."""
    path = Path(model_path)
    if path.is_dir():
        target = path / "model.ncnn.bin"
        if not target.exists():
            bins = sorted(path.glob("*.bin")) or sorted(path.glob("*.onnx")) or sorted(path.glob("*.pt"))
            if not bins:
                return "unknown"
            target = bins[0]
    elif path.is_file():
        target = path
    else:
        return "unknown"
    h = hashlib.sha256()
    with target.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


class LatencyWindow:
    """Ventana móvil de latencias de inferencia (ms) para p50/p95."""

    def __init__(self, size: int = 200) -> None:
        self._values: deque[float] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, ms: float) -> None:
        with self._lock:
            self._values.append(ms)

    def percentile(self, p: float) -> float:
        with self._lock:
            vals = list(self._values)
        return float(np.percentile(vals, p)) if vals else 0.0

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)


class InferenceWorker(threading.Thread):
    def __init__(
        self,
        settings: Settings,
        frames: LatestSlot[Frame],
        results: LatestSlot[Inference],
        on_result: Callable[[Inference, Frame], None],
        stop: threading.Event,
        log: logging.Logger,
    ) -> None:
        super().__init__(name="inference", daemon=True)
        self.s, self.frames, self.results = settings, frames, results
        self.on_result, self.stop, self.log = on_result, stop, log
        self.ready = threading.Event()
        self.latency = LatencyWindow()
        self.model_version = "unknown"
        self.last_progress = time.monotonic()  # lo usa el watchdog (Pipeline.healthy)
        self._fps_times: deque[float] = deque(maxlen=30)

    @property
    def fps_measured(self) -> float:
        if len(self._fps_times) < 2:
            return 0.0
        span = self._fps_times[-1] - self._fps_times[0]
        return (len(self._fps_times) - 1) / span if span > 0 else 0.0

    def run(self) -> None:
        from ultralytics import YOLO  # import tardío: los tests no lo necesitan

        self.model_version = compute_model_version(self.s.model_path)
        model = YOLO(str(self.s.model_path), task="detect")
        model.predict(
            np.zeros((self.s.model_imgsz, self.s.model_imgsz, 3), np.uint8),
            imgsz=self.s.model_imgsz,
            verbose=False,
        )  # warm-up
        period, seq = 1.0 / self.s.inference_fps, 0
        self.ready.set()
        self.last_progress = time.monotonic()
        while not self.stop.is_set():
            seq, frame = self.frames.wait_newer(seq, timeout=1.0)
            if frame is None:
                self.last_progress = time.monotonic()  # ocioso pero vivo; un predict colgado no llega aquí
                continue
            t0 = time.monotonic()
            try:
                # Sin resize previo: Ultralytics aplica letterbox respetando el aspecto.
                r = model.predict(
                    frame.image,
                    imgsz=self.s.model_imgsz,
                    conf=self.s.confidence_threshold,
                    iou=self.s.iou_threshold,
                    verbose=False,
                )[0]
            except Exception:  # noqa: BLE001
                self.log.exception("Fallo de inferencia; se continúa con el siguiente frame")
                self.stop.wait(0.5)
                continue
            h, w = frame.image.shape[:2]
            boxes = r.boxes
            dets = [
                Detection(
                    clase_id=int(c),
                    nombre_clase=r.names[int(c)],
                    confianza=float(p),
                    bbox_xyxy_norm=(x1 / w, y1 / h, x2 / w, y2 / h),
                )
                for c, p, (x1, y1, x2, y2) in zip(
                    boxes.cls.tolist(), boxes.conf.tolist(), boxes.xyxy.tolist(), strict=True
                )
            ]
            latency_ms = (time.monotonic() - t0) * 1000
            inf = Inference(
                frame_seq=frame.seq,
                ts_mono=frame.ts_mono,
                ts_wall=frame.ts_wall,
                detections=dets,
                latency_ms=latency_ms,
            )
            self.latency.add(latency_ms)
            self._fps_times.append(time.monotonic())
            self.last_progress = time.monotonic()
            self.results.put(inf)
            try:
                self.on_result(inf, frame)
            except Exception:  # noqa: BLE001
                self.log.exception("Fallo al procesar el resultado de inferencia")
            self.stop.wait(max(0.0, period - (time.monotonic() - t0)))
