"""Tipos compartidos entre captura, inferencia, agregador, capturas y streaming."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR
    ts_mono: float
    ts_wall: datetime  # UTC
    seq: int


@dataclass(frozen=True)
class Detection:
    clase_id: int
    nombre_clase: str
    confianza: float
    bbox_xyxy_norm: tuple[float, float, float, float]  # 0-1, independiente de la resolución

    def to_dict(self) -> dict[str, Any]:
        return {
            "clase_id": self.clase_id,
            "nombre_clase": self.nombre_clase,
            "confianza": round(self.confianza, 4),
            "bbox_xyxy_norm": [round(v, 4) for v in self.bbox_xyxy_norm],
        }


@dataclass(frozen=True)
class Inference:
    frame_seq: int
    ts_mono: float
    ts_wall: datetime
    detections: list[Detection]
    latency_ms: float


@dataclass
class ClosedEvent:
    """Evento cerrado. `payload` es el contrato v2 (5.2) SIN la sección `capture`,
    que completa captures.py. Nunca incluye la imagen."""

    payload: dict[str, Any]
    best_frame: Frame | None
    best_detections: list[Detection] = field(default_factory=list)
