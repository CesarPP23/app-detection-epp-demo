"""Agregador de detecciones anti-ruido.

Regla de negocio (ver docs/DATA_MODEL.md):
- Una clase debe verse de forma continua durante al menos MIN_SECONDS_TO_CONFIRM segundos
  Y en al menos MIN_FRAMES_TO_CONFIRM inferencias para ser un evento confirmado. Menos que eso
  es ruido y se descarta (solo se cuenta localmente).
- Mientras la misma clase siga apareciendo sin una interrupción mayor a SESSION_GAP_SECONDS es
  UNA sola sesión. Se cierra cuando deja de verse más que ese margen, o con flush_all().
- Los tiempos usan el reloj monotónico de los frames reales procesados (independiente del FPS).
- Una sesión por CLASE, no por persona (limitación D9, documentada).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import __version__
from .ids import uuid7
from .models import ClosedEvent, Detection, Frame, Inference


def iso_z(dt: datetime) -> str:
    """ISO-8601 UTC con milisegundos y sufijo Z (formato del contrato v2)."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass
class _ActiveSession:
    class_id: int
    class_name: str
    first_seen: float
    last_seen: float
    start_wall: datetime
    last_wall: datetime
    event_id: str = field(default_factory=uuid7)
    frame_count: int = 0
    confidence_sum: float = 0.0
    max_confidence: float = 0.0
    confirmed: bool = False
    best_frame: Frame | None = None
    best_detections: list[Detection] = field(default_factory=list)

    @property
    def avg_confidence(self) -> float:
        return self.confidence_sum / self.frame_count if self.frame_count else 0.0


class DetectionAggregator:
    def __init__(
        self,
        device_id: str,
        min_seconds_to_confirm: float = 1.0,
        min_frames_to_confirm: int = 3,
        session_gap_seconds: float = 4.0,
        model_version: str = "unknown",
        camera_source: str = "unknown",
        is_simulated: bool = False,
        edge_version: str = __version__,
        logger: logging.Logger | None = None,
    ) -> None:
        self.device_id = device_id
        self.min_seconds_to_confirm = min_seconds_to_confirm
        self.min_frames_to_confirm = min_frames_to_confirm
        self.session_gap_seconds = session_gap_seconds
        self.model_version = model_version
        self.camera_source = camera_source
        self.is_simulated = is_simulated
        self.edge_version = edge_version
        self._sessions: dict[str, _ActiveSession] = {}
        self._log = logger
        self.frames_processed = 0
        self.noise_discarded_count = 0
        self._last_noise_log_at = 0

    def _say(self, level: str, msg: str) -> None:
        if self._log:
            getattr(self._log, level)(msg)

    def update(self, inference: Inference, frame: Frame) -> tuple[list[ClosedEvent], bool]:
        """Devuelve (eventos cerrados en este tick, hay actividad confirmada ahora)."""
        self.frames_processed += 1
        now, now_wall = inference.ts_mono, inference.ts_wall

        # Por clase, solo la detección de mayor confianza del tick (dos cajas de la misma
        # clase no deben sumar dos veces a frame_count).
        top: dict[str, Detection] = {}
        for det in inference.detections:
            cur = top.get(det.nombre_clase)
            if cur is None or det.confianza > cur.confianza:
                top[det.nombre_clase] = det

        just_confirmed = False
        for name, det in top.items():
            s = self._sessions.get(name)
            if s is None:
                s = _ActiveSession(
                    class_id=det.clase_id,
                    class_name=name,
                    first_seen=now,
                    last_seen=now,
                    start_wall=now_wall,
                    last_wall=now_wall,
                )
                self._sessions[name] = s
            s.last_seen, s.last_wall = now, now_wall
            s.frame_count += 1
            s.confidence_sum += det.confianza
            if det.confianza > s.max_confidence:
                s.max_confidence = det.confianza
                s.best_frame = frame
                s.best_detections = list(inference.detections)  # contexto completo de la captura
            if (
                not s.confirmed
                and (now - s.first_seen) >= self.min_seconds_to_confirm
                and s.frame_count >= self.min_frames_to_confirm
            ):
                s.confirmed = True
                just_confirmed = True
                self._say(
                    "info",
                    f"Evento CONFIRMADO en vivo: '{name}' (device={self.device_id}) "
                    f"tras {now - s.first_seen:.1f}s y {s.frame_count} inferencias.",
                )

        closed: list[ClosedEvent] = []
        for name in list(self._sessions):
            s = self._sessions[name]
            if name in top or (now - s.last_seen) <= self.session_gap_seconds:
                continue  # posible oclusión corta: se da margen antes de cerrar
            del self._sessions[name]
            self._finish(s, "gap", closed)

        self._noise_summary()
        active = just_confirmed or any(s.confirmed for s in self._sessions.values())
        return closed, active

    def flush_all(self, reason: str) -> list[ClosedEvent]:
        """Cierra las sesiones abiertas (apagado, pausa o cámara perdida) para no perderlas (D1)."""
        closed: list[ClosedEvent] = []
        for name in list(self._sessions):
            self._finish(self._sessions.pop(name), reason, closed)
        return closed

    def _finish(self, s: _ActiveSession, reason: str, out: list[ClosedEvent]) -> None:
        if not s.confirmed:
            self.noise_discarded_count += 1
            self._say(
                "debug",
                f"Descartado como ruido: '{s.class_name}' duró {s.last_seen - s.first_seen:.2f}s "
                f"({s.frame_count} inferencia(s)).",
            )
            return
        # end_ts = última detección (coherente con duration_seconds; corrige D5)
        duration = (s.last_wall - s.start_wall).total_seconds()
        payload: dict[str, Any] = {
            "schema_version": 2,
            "event_id": s.event_id,
            "event_type": "deteccion_confirmada",
            "device_id": self.device_id,
            "clase_id": s.class_id,
            "nombre_clase": s.class_name,
            "confianza_promedio": round(s.avg_confidence, 4),
            "confianza_max": round(s.max_confidence, 4),
            "frame_count": s.frame_count,
            "start_ts": iso_z(s.start_wall),
            "end_ts": iso_z(s.last_wall),
            "duration_seconds": round(duration, 2),
            "is_confirmed": True,
            "closed_reason": reason,
            "is_simulated": self.is_simulated,
            "detections_at_capture": [d.to_dict() for d in s.best_detections],
            "edge": {
                "edge_version": self.edge_version,
                "model_version": self.model_version,
                "camera_source": self.camera_source,
            },
        }
        out.append(ClosedEvent(payload=payload, best_frame=s.best_frame, best_detections=s.best_detections))
        self._say(
            "info",
            f"Evento CERRADO ({reason}): '{s.class_name}' {s.frame_count} inferencias, "
            f"conf. prom. {s.avg_confidence:.2f}, {duration:.1f}s.",
        )

    def _noise_summary(self) -> None:
        if self.noise_discarded_count and self.noise_discarded_count - self._last_noise_log_at >= 10:
            self._last_noise_log_at = self.noise_discarded_count
            self._say(
                "info",
                f"Resumen anti-ruido: {self.noise_discarded_count} detecciones descartadas "
                "por corta duración.",
            )
