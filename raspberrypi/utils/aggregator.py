# aggregator.py
"""
Agregador de detecciones anti-ruido.

Problema que resuelve: a 1 detección por frame procesado, un objeto presente
durante unos pocos segundos genera decenas de filas casi idénticas, y un
falso positivo de un solo frame (parpadeo del modelo) se ve exactamente
igual que una detección real en los datos crudos.

Regla de negocio:
- Una clase debe verse de forma continua durante al menos MIN_SECONDS_TO_CONFIRM
  segundos (tiempo real, no frames crudos de video) para considerarse un
  evento "confirmado" (is_confirmed=True). Menos que eso se trata como ruido
  y se descarta (no se publica a la nube, solo se cuenta localmente).
- Mientras la misma clase se siga viendo sin una interrupción mayor a
  SESSION_GAP_SECONDS, es UNA sola sesión (un solo evento agregado), no una
  fila por frame. El evento se cierra y se publica cuando la clase deja de
  verse por más de SESSION_GAP_SECONDS.
- Los tiempos se miden con time.monotonic() sobre los "ticks" de
  procesamiento real (cuando efectivamente corre YOLO), así el criterio es
  independiente del FPS del video o de la cámara.

Valores por defecto (ajustables por el usuario):
- CONFIDENCE_THRESHOLD = 0.5   -> ya se aplica antes de llegar aquí (detector).
- MIN_SECONDS_TO_CONFIRM = 1.0 -> filtra parpadeos de 1 frame del modelo.
- SESSION_GAP_SECONDS = 4.0    -> tolera oclusiones cortas (alguien se agacha,
                                   pasa un objeto por delante, etc.) sin cortar
                                   la sesión en dos eventos separados.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class _ActiveSession:
    class_id: int
    class_name: str
    first_seen: float
    last_seen: float
    frame_count: int = 0
    confidence_sum: float = 0.0
    max_confidence: float = 0.0
    confirmed: bool = False
    start_ts_iso: str = ""

    def register(self, now: float, confidence: float):
        self.last_seen = now
        self.frame_count += 1
        self.confidence_sum += confidence
        if confidence > self.max_confidence:
            self.max_confidence = confidence

    @property
    def avg_confidence(self) -> float:
        return self.confidence_sum / self.frame_count if self.frame_count else 0.0


class DetectionAggregator:
    def __init__(
        self,
        device_id: str,
        min_seconds_to_confirm: float = 1.0,
        session_gap_seconds: float = 4.0,
        logger=None,
    ):
        self.device_id = device_id
        self.min_seconds_to_confirm = min_seconds_to_confirm
        self.session_gap_seconds = session_gap_seconds
        self._sessions: dict[str, _ActiveSession] = {}
        self._logger = logger
        self.frames_processed = 0
        self.noise_discarded_count = 0
        self._last_noise_log_at = 0

    def _log(self, level: str, msg: str):
        if self._logger:
            getattr(self._logger, level)(msg)

    def update(self, detections: list[dict], now: float) -> tuple[list[dict], bool]:
        """
        detections: lista de dicts con al menos nombre_clase/clase_id/confianza
                    (formato ya usado en el resto del proyecto), ya filtrados
                    por CONFIDENCE_THRESHOLD antes de llegar aquí.
        now: time.monotonic() del tick actual.

        Devuelve (eventos_confirmados_cerrados_este_tick, hay_actividad_confirmada_ahora)
        """
        self.frames_processed += 1
        seen_this_tick = set()
        just_confirmed_now = False

        for det in detections:
            class_name = det["nombre_clase"]
            class_id = det["clase_id"]
            confidence = det["confianza"]
            seen_this_tick.add(class_name)

            session = self._sessions.get(class_name)
            if session is None:
                session = _ActiveSession(
                    class_id=class_id,
                    class_name=class_name,
                    first_seen=now,
                    last_seen=now,
                    start_ts_iso=datetime.now(timezone.utc).isoformat(),
                )
                self._sessions[class_name] = session

            session.register(now, confidence)

            if not session.confirmed and (now - session.first_seen) >= self.min_seconds_to_confirm:
                session.confirmed = True
                just_confirmed_now = True
                self._log(
                    "info",
                    f"Evento CONFIRMADO en vivo: '{class_name}' (device={self.device_id}) "
                    f"tras {now - session.first_seen:.1f}s continuos por encima del umbral.",
                )

        closed_events: list[dict] = []
        for class_name in list(self._sessions.keys()):
            session = self._sessions[class_name]
            if class_name in seen_this_tick:
                continue
            if (now - session.last_seen) <= self.session_gap_seconds:
                continue  # posible oclusión corta, damos margen antes de cerrar

            # Se cierra la sesión (no se vio en este tick y superó el gap)
            del self._sessions[class_name]
            if session.confirmed:
                end_ts_iso = datetime.now(timezone.utc).isoformat()
                closed_events.append(
                    {
                        "event_type": "deteccion_confirmada",
                        "device_id": self.device_id,
                        "clase_id": session.class_id,
                        "nombre_clase": session.class_name,
                        "confianza_promedio": round(session.avg_confidence, 4),
                        "confianza_max": round(session.max_confidence, 4),
                        "frame_count": session.frame_count,
                        "start_ts": session.start_ts_iso,
                        "end_ts": end_ts_iso,
                        "duration_seconds": round(session.last_seen - session.first_seen, 2),
                        "is_confirmed": True,
                    }
                )
                self._log(
                    "info",
                    f"Evento CERRADO y publicable: '{session.class_name}' "
                    f"({session.frame_count} frames, {session.avg_confidence:.2f} conf. prom., "
                    f"{session.last_seen - session.first_seen:.1f}s).",
                )
            else:
                self.noise_discarded_count += 1
                self._log(
                    "debug",
                    f"Descartado como ruido: '{session.class_name}' solo duró "
                    f"{session.last_seen - session.first_seen:.2f}s "
                    f"(< {self.min_seconds_to_confirm}s requeridos, {session.frame_count} frame(s)).",
                )

        # Resumen periódico de ruido filtrado, para poder auditar sin saturar el log
        if self.noise_discarded_count and self.noise_discarded_count - self._last_noise_log_at >= 10:
            self._last_noise_log_at = self.noise_discarded_count
            self._log(
                "info",
                f"Resumen anti-ruido: {self.noise_discarded_count} detecciones descartadas "
                f"por corta duración desde el inicio (no llegaron a evento confirmado).",
            )

        any_active_confirmed = just_confirmed_now or any(s.confirmed for s in self._sessions.values())
        return closed_events, any_active_confirmed
