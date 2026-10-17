"""Captura de fotos crudas (sin anotar) para reentrenamiento, bajo control remoto.

Mientras `DeviceControl.should_capture()` sea verdadero, `CaptureWorker` toma fotos de la cámara
(el frame crudo, no el `FrameBuffer` anotado que usa `StreamWorker` en `cloud_v2.py`, porque ese
tiene cajas/esqueleto dibujados y contaminaría el dataset) y las sube a
`raw/training_captures/device=<id>/session=<session_id>/<photo_id>.jpg`, con el mismo patrón
idempotente (`if_generation_match=0`) que usa `Uploader` en `cloud_v2.py`.

El documento `training_sessions/{session_id}` (misma base de Firestore que los episodios) lleva
la cuenta de fotos subidas (actualizada cada pocos segundos, no por foto, para no amplificar
escrituras) y se cierra con `status="completed"` cuando `capture_requested` pasa a `false`, cambia
el `session_id`, o se supera `max_duration_s` (salvaguarda por si el operador olvida detenerla).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from .cloud_v2 import uuid7

log = logging.getLogger("epp.training_capture")

TRAINING_SESSIONS_COLLECTION = "training_sessions"


class CaptureWorker(threading.Thread):
    """Sube fotos crudas a la carpeta de reentrenamiento mientras haya una sesión de captura activa."""

    def __init__(self, cam, control, bucket, events_db, device_id: str, *, fps: float = 1.0,
                 jpeg_quality: int = 85, max_duration_s: float = 3600.0,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(name="training-capture", daemon=True)
        self.cam, self.control, self.bucket, self.db, self.device_id = cam, control, bucket, events_db, device_id
        self.fps, self.jpeg_quality, self.max_duration_s = fps, jpeg_quality, max_duration_s
        self._clock = clock
        self._stop = threading.Event()
        self._session_id: Optional[str] = None
        self._session_started = 0.0
        self._force_closed_session_id: Optional[str] = None
        self._last_flush = 0.0
        self._pending_increment = 0
        self._last_frame_id = 0
        self.photo_count = 0
        self.uploaded = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        period = 1.0 / max(self.fps, 0.01)
        while not self._stop.is_set():
            t0 = self._clock()
            self.tick()
            self._stop.wait(max(0.0, period - (self._clock() - t0)))
        if self._session_id is not None:  # el proceso se apaga con una sesión abierta: la cerramos igual
            self._close_session("stopped_by_operator")

    def tick(self) -> None:
        try:
            self._tick()
        except Exception as e:  # noqa: BLE001
            log.warning("Captura de entrenamiento: fallo en el ciclo (%s)", e)

    def _tick(self) -> None:
        requested = self.control.should_capture()
        session_id = self.control.capture_session() if requested else None
        if not requested or session_id is None:
            if self._session_id is not None:
                self._close_session("stopped_by_operator")
            self._force_closed_session_id = None  # ya no hay sesión remota activa: olvida el veto
            return
        if session_id == self._force_closed_session_id:
            # esta sesión ya se cerró por max_duration_s: no la reabrimos solo porque el flag
            # remoto siga en true (sin esto, el worker la reabriría cada ciclo y la salvaguarda
            # nunca detendría nada). Se vuelve a capturar recién cuando llegue un session_id nuevo.
            return
        if session_id != self._session_id:
            if self._session_id is not None:
                self._close_session("stopped_by_operator")
            self._open_session(session_id)
        if self.max_duration_s and (self._clock() - self._session_started) > self.max_duration_s:
            self._force_closed_session_id = session_id
            self._close_session("max_duration")
            return
        frame, fid, _ts = self.cam.wait_new(self._last_frame_id, timeout=1.0)
        if frame is None:
            return
        self._last_frame_id = fid
        self._upload_photo(frame)
        self._maybe_flush_count()

    def _open_session(self, session_id: str) -> None:
        self._session_id = session_id
        self._session_started = self._clock()
        self.photo_count = 0
        self._pending_increment = 0
        self._last_flush = self._clock()
        log.info("Captura de entrenamiento iniciada: sesión %s", session_id)

    def _upload_photo(self, frame: Any) -> None:
        import cv2  # import tardío, igual criterio que el resto del nodo
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            return
        photo_id = uuid7()
        path = f"raw/training_captures/device={self.device_id}/session={self._session_id}/{photo_id}.jpg"
        data = buf.tobytes()
        for attempt in range(2):
            try:
                self._upload(path, data)
                self.photo_count += 1
                self._pending_increment += 1
                self.uploaded += 1
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 0:
                    time.sleep(0.5)
                else:
                    log.warning("No se pudo subir foto de entrenamiento %s: %s", photo_id, e)

    def _upload(self, path: str, data: bytes) -> None:
        from google.api_core.exceptions import PreconditionFailed  # import tardío
        blob = self.bucket.blob(path)
        try:
            blob.upload_from_string(data, content_type="image/jpeg", if_generation_match=0)
        except PreconditionFailed:
            pass  # ya existía: la subida es idempotente

    def _maybe_flush_count(self, force: bool = False) -> None:
        if not self._pending_increment:
            return
        if not force and (self._clock() - self._last_flush) < 5.0:
            return
        from google.cloud import firestore  # import tardío
        n = self._pending_increment
        try:
            self.db.collection(TRAINING_SESSIONS_COLLECTION).document(self._session_id).set(
                {"device_id": self.device_id, "session_id": self._session_id,
                 "frame_count": firestore.Increment(n), "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
            self._pending_increment -= n
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo actualizar el contador de la sesión %s: %s", self._session_id, e)
        self._last_flush = self._clock()

    def _close_session(self, reason: str) -> None:
        self._maybe_flush_count(force=True)
        from google.cloud import firestore  # import tardío
        try:
            self.db.collection(TRAINING_SESSIONS_COLLECTION).document(self._session_id).set(
                {"status": "completed", "closed_reason": reason, "ended_at": firestore.SERVER_TIMESTAMP}, merge=True)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo cerrar la sesión de captura %s: %s", self._session_id, e)
        log.info("Captura de entrenamiento detenida: sesión %s (%s, %d fotos)",
                 self._session_id, reason, self.photo_count)
        self._session_id = None
