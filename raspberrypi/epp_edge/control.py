"""Control por listener de Firestore y reporte de salud (2.10).

`device_control/{id}` lo escribe solo el servidor del dashboard; `device_health/{id}` solo la Pi.
Ante un error de Firestore se CONSERVA el último estado conocido (corrige D6): el valor por
defecto solo se usa si nunca hubo una lectura exitosa.
"""
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

CONTROL_COLLECTION = "device_control"
HEALTH_COLLECTION = "device_health"
_VALID_STATES = {"running", "stopped"}


class DeviceControl:
    def __init__(self, db, s, log: logging.Logger, clock=time.monotonic) -> None:
        self.s, self.log, self._clock = s, log, clock
        self._doc = db.collection(CONTROL_COLLECTION).document(s.device_id)
        self.default_state = s.default_desired_state
        self.desired_state = self.default_state
        self._lock = threading.Lock()
        self._lease_deadline = 0.0
        self._last_renewed = None
        self._auto_until = 0.0
        self.last_sync = 0.0
        self._ever_synced = False
        self._watch = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- estado --

    def _apply(self, snap) -> None:
        """Aplica un DocumentSnapshot (o None = documento inexistente)."""
        data = (snap.to_dict() or {}) if snap is not None and snap.exists else {}
        with self._lock:
            desired = data.get("desired_state")
            self.desired_state = desired if desired in _VALID_STATES else self.default_state
            renewed, lease = data.get("lease_renewed_at"), float(data.get("lease_seconds", 90))
            if not data.get("stream_requested") or renewed is None:
                self._lease_deadline = 0.0
                self._last_renewed = None
            elif renewed != self._last_renewed:
                # read_time y lease_renewed_at son hora del SERVIDOR: no hay desfase con el reloj de la Pi.
                age = (snap.read_time - renewed).total_seconds()
                self._lease_deadline = self._clock() + max(0.0, lease - age)
                self._last_renewed = renewed
            self.last_sync = self._clock()
            self._ever_synced = True

    def should_run(self) -> bool:
        with self._lock:
            return self.desired_state == "running"

    def lease_active(self) -> bool:
        with self._lock:
            return self._clock() < self._lease_deadline

    def should_stream(self) -> bool:
        with self._lock:
            now = self._clock()
            return self.desired_state == "running" and (now < self._lease_deadline or now < self._auto_until)

    def bump_auto_stream(self, seconds: float) -> None:
        """Solo con AUTO_STREAM_ON_DETECTION=true (comportamiento viejo y costoso)."""
        if not self.s.auto_stream_on_detection:
            return
        with self._lock:
            self._auto_until = max(self._auto_until, self._clock() + seconds)

    # -- ciclo de vida --

    def start(self) -> None:
        self._subscribe()
        self._thread = threading.Thread(target=self._safety_loop, name="control-safety", daemon=True)
        self._thread.start()

    def _subscribe(self) -> None:
        try:
            self._watch = self._doc.on_snapshot(self._on_snapshot)
        except Exception:  # noqa: BLE001
            self.log.exception("No se pudo suscribir a %s; se usará el sondeo de respaldo", CONTROL_COLLECTION)
            self._watch = None

    def _on_snapshot(self, docs, changes, read_time) -> None:
        # Lista vacía = el documento no existe (se aplican los valores por defecto).
        self._apply(docs[0] if docs else None)

    def safety_tick(self) -> None:
        """Respaldo del listener: get() + _apply(); resuscribe si no hay sincronía hace 2 intervalos."""
        poll = self.s.control_safety_poll_seconds
        try:
            self._apply(self._doc.get())
        except Exception as e:  # noqa: BLE001
            self.log.warning("Lectura de respaldo de %s falló (se conserva el estado): %s", CONTROL_COLLECTION, e)
        if self._clock() - self.last_sync > 2 * poll:
            self.log.warning("Sin sincronía con Firestore hace más de %ds; se vuelve a suscribir", 2 * poll)
            self._unsubscribe()
            self._subscribe()

    def _safety_loop(self) -> None:
        while not self._stop.wait(self.s.control_safety_poll_seconds):
            self.safety_tick()

    def _unsubscribe(self) -> None:
        try:
            if self._watch is not None:
                self._watch.unsubscribe()
        except Exception:  # noqa: BLE001
            pass
        self._watch = None

    def close(self) -> None:
        self._stop.set()
        self._unsubscribe()


def _cpu_temp_c() -> float | None:
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None


class HealthReporter(threading.Thread):
    """Escribe `device_health` cada HEARTBEAT_SECONDS y en cada transición (debounce de 2 s)."""

    DEBOUNCE_SECONDS = 2.0

    def __init__(self, db, s, outbox, log: logging.Logger, edge_version: str = "unknown") -> None:
        super().__init__(name="health", daemon=True)
        self.s, self.outbox, self.log = s, outbox, log
        self._doc = db.collection(HEALTH_COLLECTION).document(s.device_id)
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self._halt = threading.Event()
        self.state: dict[str, Any] = {
            "reported_state": "stopped",
            "streaming": False,
            "camera_ok": False,
            "camera_source": "unknown",
            "inference_fps": 0.0,
            "inference_ms_p50": 0.0,
            "inference_ms_p95": 0.0,
            "edge_version": edge_version,
            "model_version": "unknown",
        }

    def update(self, **fields: Any) -> None:
        """Actualiza campos; si cambia algo, programa una escritura inmediata (con debounce)."""
        with self._lock:
            changed = any(self.state.get(k) != v for k, v in fields.items())
            self.state.update(fields)
        if changed:
            self._dirty.set()

    def set_streaming(self, on: bool) -> None:
        self.update(streaming=bool(on))

    def set_reported_state(self, state: str) -> None:
        self.update(reported_state=state)

    def run(self) -> None:
        self.write()  # latido inicial: el dashboard ve el dispositivo apenas arranca
        while not self._halt.is_set():
            woke = self._dirty.wait(self.s.heartbeat_seconds)
            if self._halt.is_set():
                break
            if woke:
                self._halt.wait(self.DEBOUNCE_SECONDS)  # agrupa transiciones seguidas
                self._dirty.clear()
            self.write()

    def write(self) -> None:
        from google.cloud import firestore  # import tardío

        with self._lock:
            doc = dict(self.state)
        st = self.outbox.stats()
        doc.update(
            outbox_pending=st["pending"],
            outbox_bytes=st["bytes"],
            cpu_temp_c=_cpu_temp_c(),
            last_seen=firestore.SERVER_TIMESTAMP,
        )
        try:
            self._doc.set(doc, merge=True)
        except Exception as e:  # noqa: BLE001
            self.log.warning("No se pudo escribir device_health: %s", e)
        self._ping(doc["camera_ok"])

    def _ping(self, camera_ok: bool) -> None:
        url = self.s.healthcheck_url
        if not url:
            return
        try:
            urllib.request.urlopen(url if camera_ok else url.rstrip("/") + "/fail", timeout=5).close()  # noqa: S310
        except Exception as e:  # noqa: BLE001
            self.log.warning("Healthcheck externo falló: %s", e)

    def final(self, state: str = "offline") -> None:
        """Apagado: detiene el hilo y deja `reported_state` en `offline`."""
        self._halt.set()
        self._dirty.set()
        self.update(reported_state=state, streaming=False)
        self.write()
