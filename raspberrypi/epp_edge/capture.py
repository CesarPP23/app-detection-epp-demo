"""Fuentes de video y el hilo de captura. Nunca cae en silencio al video de prueba (D2)."""

import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

import cv2
import numpy as np

from .config import Settings
from .models import Frame
from .slots import LatestSlot


class CaptureError(RuntimeError):
    pass


class PiCameraSource:
    name = "picamera2"

    def __init__(self, s: Settings) -> None:
        self.s = s
        self._cam = None

    def open(self) -> None:
        try:
            from picamera2 import Picamera2  # import tardío: solo existe en la Pi
        except ImportError as e:
            raise CaptureError("picamera2 no está instalado") from e
        try:
            cam = Picamera2()
            # RGB888 en picamera2 entrega los píxeles en orden B,G,R: el que usa OpenCV.
            cfg = cam.create_video_configuration(
                main={"size": (self.s.capture_width, self.s.capture_height), "format": "RGB888"},
                buffer_count=2,
                controls={"FrameRate": self.s.capture_fps},
            )
            cam.configure(cfg)
            cam.start()
        except Exception as e:  # noqa: BLE001 - picamera2 lanza tipos variados
            raise CaptureError(f"no se pudo abrir la cámara CSI: {e}") from e
        self._cam = cam

    def read(self) -> np.ndarray | None:
        if self._cam is None:
            return None
        try:
            return self._cam.capture_array("main")
        except Exception:  # noqa: BLE001
            return None

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            finally:
                self._cam = None


class OpenCVSource:
    def __init__(self, s: Settings, index: int) -> None:
        self.s, self.index = s, index
        self.name = f"usb:{index}"
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
            raise CaptureError(f"no se pudo abrir la cámara USB {self.index}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.s.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.s.capture_height)
        cap.set(cv2.CAP_PROP_FPS, self.s.capture_fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, img = self._cap.read()
        return img if ok else None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class FileSource:
    """Video de prueba en bucle, respetando el FPS del archivo. Solo con ALLOW_TEST_VIDEO=true."""

    def __init__(self, s: Settings, path: str) -> None:
        if not s.allow_test_video:
            raise CaptureError("el video de prueba requiere ALLOW_TEST_VIDEO=true")
        self.path = path
        self.name = f"file:{path}"
        self._cap: cv2.VideoCapture | None = None
        self._period = 1 / 30
        self._next = 0.0

    def open(self) -> None:
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            cap.release()
            raise CaptureError(f"no se pudo abrir el archivo de video {self.path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        self._period = 1.0 / fps if fps and fps > 0 else 1 / 30
        self._next = time.monotonic()
        self._cap = cap

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next = max(self._next, time.monotonic() - self._period) + self._period
        ok, img = self._cap.read()
        if not ok:  # fin del archivo: vuelve al inicio
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, img = self._cap.read()
        return img if ok else None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


def build_source(s: Settings):
    """Abre la fuente configurada. `auto` prueba picamera2 y luego usb:0; si ninguna abre
    lanza CaptureError (NUNCA usa el video de prueba)."""
    src = s.camera_source
    if src.startswith("file:"):
        source = FileSource(s, src[5:])
        source.open()
        return source
    if src == "picamera2":
        candidates = [PiCameraSource(s)]
    elif src.startswith("usb:"):
        candidates = [OpenCVSource(s, int(src[4:]))]
    else:  # auto
        candidates = [PiCameraSource(s), OpenCVSource(s, 0)]
    errors = []
    for cand in candidates:
        try:
            cand.open()
            return cand
        except CaptureError as e:
            errors.append(str(e))
    raise CaptureError("; ".join(errors))


class CaptureThread(threading.Thread):
    """Lee la cámara y deja siempre el frame más reciente en el slot."""

    MAX_EMPTY_READS = 10

    def __init__(
        self,
        settings: Settings,
        slot: LatestSlot[Frame],
        stop: threading.Event,
        log: logging.Logger,
        source_factory: Callable[[Settings], object] = build_source,
    ) -> None:
        super().__init__(name="capture", daemon=True)
        self.s, self.slot, self.stop_event, self.log = settings, slot, stop, log
        self._factory = source_factory
        self._times: deque[float] = deque(maxlen=60)
        self.ok = False
        self.source_name = ""
        self._seq = 0

    @property
    def fps_measured(self) -> float:
        if len(self._times) < 2:
            return 0.0
        span = self._times[-1] - self._times[0]
        return (len(self._times) - 1) / span if span > 0 else 0.0

    def run(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                source = self._factory(self.s)
            except CaptureError as e:
                self.ok = False
                self.log.error("Cámara no disponible: %s (reintento en %.0fs)", e, backoff)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            self.ok, self.source_name, backoff = True, source.name, 1.0
            self.log.info("Cámara abierta: %s", source.name)
            empty = 0
            try:
                while not self.stop_event.is_set():
                    img = source.read()
                    if img is None:
                        empty += 1
                        if empty >= self.MAX_EMPTY_READS:
                            self.log.error("La cámara dejó de entregar frames; se reabrirá")
                            break
                        time.sleep(0.05)
                        continue
                    empty = 0
                    self._seq += 1
                    now = time.monotonic()
                    self._times.append(now)
                    self.slot.put(Frame(img, now, datetime.now(UTC), self._seq))
            finally:
                self.ok = False
                source.close()
            self.stop_event.wait(1.0)  # pausa antes de reabrir
