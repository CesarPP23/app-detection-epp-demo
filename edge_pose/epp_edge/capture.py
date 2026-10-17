"""Captura de video: webcam USB (V4L2), archivo de video (pruebas) o cámara IP (RTSP).

Corre en un hilo propio y siempre expone el ÚLTIMO frame disponible, de modo que
los consumidores nunca se quedan procesando frames viejos (importante para el Pi,
donde la inferencia es más lenta que la cámara).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("epp.capture")


class Camera:
    def __init__(self, source, width: int, height: int, fps: int, flip: bool = False,
                 loop_video: bool = True, base_dir: Optional[Path] = None, controls: Optional[dict] = None):
        self.source = source
        self.controls = controls or {}
        self.width, self.height, self.fps, self.flip = width, height, fps, flip
        self.loop_video = loop_video
        self.is_file = isinstance(source, str) and not source.startswith(("rtsp://", "http://", "https://"))
        if self.is_file and base_dir is not None and not Path(source).is_absolute():
            self.source = str((base_dir / source).resolve())
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_id = 0
        self._ts = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.reconnects = 0

    # ------------------------------------------------------------------ setup
    def _open(self) -> bool:
        if self._cap is not None:
            self._cap.release()
        if isinstance(self.source, int):
            # V4L2 + MJPG reduce el uso de CPU/USB frente a YUYV en Raspberry Pi
            cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else cv2.VideoCapture(self.source)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.source)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # controles opcionales (exposición fija, ganancia...) para evitar oscilaciones del auto-exposure
            props = {"auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE, "exposure": cv2.CAP_PROP_EXPOSURE,
                     "gain": cv2.CAP_PROP_GAIN, "brightness": cv2.CAP_PROP_BRIGHTNESS, "contrast": cv2.CAP_PROP_CONTRAST,
                     "saturation": cv2.CAP_PROP_SATURATION, "auto_wb": cv2.CAP_PROP_AUTO_WB, "wb": cv2.CAP_PROP_WB_TEMPERATURE}
            for k, v in self.controls.items():
                if k in props and not cap.set(props[k], float(v)):
                    log.warning("La cámara no aceptó el control %s=%s", k, v)
        else:
            cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            log.error("No se pudo abrir la fuente de video: %s", self.source)
            self._cap = None
            return False
        self._cap = cap
        log.info("Cámara abierta: %s (%sx%s @ %s fps solicitado)", self.source,
                 int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                 cap.get(cv2.CAP_PROP_FPS))
        return True

    def start(self) -> "Camera":
        self._open()
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap is not None:
            self._cap.release()

    # ------------------------------------------------------------------- loop
    def _loop(self) -> None:
        file_delay = 1.0 / max(self.fps, 1) if self.is_file else 0.0
        fails = 0
        while not self._stop.is_set():
            if self._cap is None:
                time.sleep(2)
                self.reconnects += 1
                self._open()
                continue
            ok, frame = self._cap.read()
            if not ok or frame is None:
                if self.is_file and self.loop_video:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                fails += 1
                if fails > 30:
                    log.warning("Cámara sin frames; reconectando (%s)", self.source)
                    fails = 0
                    self.reconnects += 1
                    self._open()
                time.sleep(0.05)
                continue
            fails = 0
            if self.is_file and frame.shape[1] != self.width:
                # archivos de video: se escala al ancho configurado manteniendo la relación de aspecto
                h = int(frame.shape[0] * self.width / frame.shape[1])
                frame = cv2.resize(frame, (self.width, h), interpolation=cv2.INTER_AREA)
            if self.flip:
                frame = cv2.flip(frame, 1)
            with self._lock:
                self._frame = frame
                self._frame_id += 1
                self._ts = time.time()
            if file_delay:
                time.sleep(file_delay)

    # ------------------------------------------------------------------ access
    def read(self) -> tuple[Optional[np.ndarray], int, float]:
        """Devuelve (frame, id, timestamp). El frame es una referencia; no modificar in-place."""
        with self._lock:
            return self._frame, self._frame_id, self._ts

    def wait_new(self, last_id: int, timeout: float = 1.0) -> tuple[Optional[np.ndarray], int, float]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            frame, fid, ts = self.read()
            if frame is not None and fid != last_id:
                return frame, fid, ts
            time.sleep(0.005)
        return None, last_id, 0.0

    @property
    def healthy(self) -> bool:
        return self._cap is not None and (time.time() - self._ts) < 5.0
