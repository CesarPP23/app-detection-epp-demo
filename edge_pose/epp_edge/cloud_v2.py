"""Integración del nodo con la nube de la versión 2.0.0 (repositorio `app-detection-epp-demo`).

La lógica del modelo es la de este nodo (YOLOv8n-pose + YOLOv8n EPP, máquina de estados, reglas de colocación y
episodios). Este módulo solo cambia CÓMO salen los episodios hacia la nube, para usar los servicios de esa versión:

  * Contrato de evento v2 con `event_id` UUIDv7 (RFC 9562): el mismo identificador es el nombre de los objetos en el
    lago de datos, el ID del documento en Firestore y la clave en BigQuery.
  * Outbox idempotente en la misma base SQLite del nodo: etapas images -> sidecar -> firestore. El hilo `Uploader`
    sube la imagen original y la miniatura anotada a `raw/images` y `raw/thumbs`, el sidecar JSON a `raw/events`
    (siempre con `if_generation_match=0`: solo crear) y escribe el documento del episodio + `daily_stats` en un batch
    atómico de Firestore. Reintentos con espera exponencial min(300, 2^n) s. Un reintento nunca duplica.
  * `DeviceControl`: escucha `(default)/device_control/{device_id}` (desired_state, lease de video) con un sondeo de
    respaldo y conserva el último estado conocido si Firestore falla.
  * `HealthReporter`: escribe `device_health/{device_id}` cada `heartbeat_s` y en cada transición.
  * `StreamWorker`: video solo con lease vigente: wss://<host>/ws/video/{device_id}, `Authorization: Bearer
    DEVICE_TOKEN`, cada mensaje = 8 bytes (uint64 big-endian, ms de captura) + JPEG.

Compatibilidad con el panel de la versión 2.0.0: cada episodio es un evento con
`nombre_clase = "episodio_conforme" | "episodio_no_conforme"` (el panel lo muestra como «episodio conforme» /
«episodio no conforme» y lo cuenta en `daily_stats.by_class`), las cajas de cofia, guante y mascarilla van en
`detections_at_capture`, y los campos propios del episodio (cumple, faltantes, mal_colocados, personas, ratios) viajan
como campos adicionales que la API ignora sin romperse.

Las dependencias de Google (`google-cloud-storage`, `google-cloud-firestore`) y `websocket-client` se importan tarde:
el nodo funciona sin ellas si la nube está desactivada, y las pruebas usan clientes falsos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("epp.cloud_v2")

SCHEMA_VERSION = 2
EDGE_VERSION = "pose-2.0.0"
EVENTS_COLLECTION = "detecciones_confirmadas"
STATS_COLLECTION = "daily_stats"
CONTROL_COLLECTION = "device_control"
HEALTH_COLLECTION = "device_health"
CLASS_IDS = {"Cofia": 0, "Guante": 1, "Mascarilla": 2}
EPISODE_CLASS = {True: (100, "episodio_conforme"), False: (101, "episodio_no_conforme")}
STAGES = ("images", "sidecar", "firestore", "done")


# ----------------------------------------------------------------------------- identificadores y fechas
def uuid7(ms: Optional[int] = None) -> str:
    """UUID versión 7 (RFC 9562): 48 bits de tiempo en ms + 74 bits aleatorios; ordenable por tiempo."""
    if ms is None and hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    ms = time.time_ns() // 1_000_000 if ms is None else ms
    rand = int.from_bytes(os.urandom(10), "big")
    value = (((ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (((rand >> 68) & 0xFFF) << 64)
             | (0b10 << 62) | (rand & ((1 << 62) - 1)))
    return str(uuid.UUID(int=value))


def parse_ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def iso_z(v: Any) -> str:
    return parse_ts(v).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_day(end_ts: Any, tz: str = "America/Lima") -> str:
    return parse_ts(end_ts).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def lake_path(kind: str, device_id: str, end_ts: Any, event_id: str, tz: str = "America/Lima") -> str:
    """raw/<kind>/device=<id>/dt=<fecha local>/<event_id>.<ext>  (particiones estilo Hive)."""
    ext = "json" if kind == "events" else "jpg"
    return f"raw/{kind}/device={device_id}/dt={local_day(end_ts, tz)}/{event_id}.{ext}"


def model_version(model_dir: Path) -> str:
    """SHA-256 corto (12) del archivo de pesos NCNN del detector de EPP."""
    p = Path(model_dir)
    f = p / "model.ncnn.bin" if p.is_dir() else p
    try:
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except OSError:
        return "unknown"


def _sha256(path: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    if not path or not Path(path).exists():
        return None, None
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _json_list(v: Any) -> list:
    if isinstance(v, str):
        try:
            return json.loads(v) if v else []
        except ValueError:
            return []
    return list(v or [])


# ----------------------------------------------------------------------------- contrato del evento
def build_event(ep: dict, event_id: str, *, edge: dict, is_simulated: bool, thumb_path: Optional[str] = None) -> dict:
    """Episodio (fila SQLite del nodo) -> evento v2 compatible con la API y el panel de la versión 2.0.0."""
    cumple = bool(ep.get("cumple"))
    clase_id, nombre = EPISODE_CLASS[cumple]
    dets = _json_list(ep.get("best_dets"))
    raw = ep.get("snapshot_raw")
    sha, size = _sha256(raw)
    tsha, tsize = _sha256(thumb_path)
    capture_ok = bool(raw and Path(raw).exists())
    w = h = None
    if dets:
        w, h = dets[0].get("img_w"), dets[0].get("img_h")
    confs = [d["confianza"] for d in dets] or [0.0]
    conf_mean = ep.get("conf_mean")
    conf_max = ep.get("conf_max")
    ev = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": "episodio",
        "device_id": ep["device_id"],
        "zona": ep.get("zona"),
        "clase_id": clase_id,
        "nombre_clase": nombre,
        "confianza_promedio": round(float(conf_mean if conf_mean is not None else sum(confs) / len(confs)), 4),
        "confianza_max": round(float(conf_max if conf_max is not None else max(confs)), 4),
        "frame_count": int(ep.get("frames") or 0),
        "start_ts": iso_z(ep["ts_inicio"]),
        "end_ts": iso_z(ep.get("ts_fin") or ep["ts_inicio"]),
        "duration_seconds": round(float(ep.get("duracion_s") or 0.0), 2),
        "is_confirmed": True,
        "closed_reason": ep.get("closed_reason") or "gap",
        "is_simulated": bool(is_simulated),
        "capture": {
            "status": "ok" if capture_ok else "missing",
            "uri": None, "thumb_uri": None,            # los completa el Uploader con el bucket configurado
            "captured_at": iso_z(ep.get("best_ts") or ep.get("ts_fin") or ep["ts_inicio"]),
            "sha256": sha, "width": w, "height": h, "bytes": size,
            "thumb_sha256": tsha, "thumb_bytes": tsize,
            "confidence_at_capture": round(max(confs), 4) if dets else None,
        },
        "detections_at_capture": [
            {"clase_id": d["clase_id"], "nombre_clase": d["nombre_clase"], "confianza": d["confianza"],
             "bbox_xyxy_norm": d["bbox_xyxy_norm"]} for d in dets],
        "edge": edge,
        # ---- campos propios del episodio (la API los conserva en Firestore y BigQuery; el panel los ignora)
        "cumple": cumple,
        "faltantes": _json_list(ep.get("faltantes")),
        "mal_colocados": _json_list(ep.get("mal_colocados")),
        "no_evaluables": _json_list(ep.get("no_evaluables")),
        "personas_max": int(ep.get("personas_max") or 0),
        "n_personas": int(ep.get("n_personas") or 0),
        "personas": _json_list(ep.get("personas")),
        "ratios": {"cofia": ep.get("ratio_cofia"), "guante": ep.get("ratio_guante"),
                   "mascarilla": ep.get("ratio_mascarilla")},
        "epp_fps": ep.get("epp_fps"),
        "episodio_local_id": ep["id"],
    }
    return ev


def to_firestore(ev: dict) -> dict:
    """Mismo contrato con Timestamps nativos y `created_at` del servidor."""
    from google.cloud import firestore  # import tardío
    doc = json.loads(json.dumps(ev))
    doc["start_ts"] = parse_ts(doc["start_ts"])
    doc["end_ts"] = parse_ts(doc["end_ts"])
    if doc.get("capture", {}).get("captured_at"):
        doc["capture"]["captured_at"] = parse_ts(doc["capture"]["captured_at"])
    doc["created_at"] = firestore.SERVER_TIMESTAMP
    return doc


def image_metadata(ev: dict, kind: str) -> dict[str, str]:
    cap = ev.get("capture") or {}
    meta = {"event_id": ev["event_id"], "device_id": ev["device_id"], "nombre_clase": ev["nombre_clase"],
            "confianza_max": ev["confianza_max"], "model_version": (ev.get("edge") or {}).get("model_version"),
            "captured_at": cap.get("captured_at"), "sha256": cap.get("thumb_sha256" if kind == "thumbs" else "sha256"),
            "kind": "thumb" if kind == "thumbs" else "original"}
    return {k: str(v) for k, v in meta.items() if v is not None}


def make_thumb(src: str, dst: str, width: int = 480, quality: int = 75) -> Optional[str]:
    """Miniatura anotada (la que muestra el panel) a partir del snapshot anotado del episodio."""
    import cv2
    img = cv2.imread(src) if src and Path(src).exists() else None
    if img is None:
        return None
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(dst, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return dst


# ----------------------------------------------------------------------------- outbox (misma SQLite del nodo)
OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox_v2 (
    episodio_id TEXT PRIMARY KEY,
    event_id    TEXT UNIQUE NOT NULL,
    stage       TEXT NOT NULL DEFAULT 'images',
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_ts     REAL NOT NULL DEFAULT 0,
    last_error  TEXT,
    payload     TEXT,
    created_at  TEXT NOT NULL
);
"""


@dataclass
class OutboxRow:
    episodio_id: str
    event_id: str
    stage: str
    attempts: int


class OutboxV2:
    """Cola idempotente: el `event_id` se asigna UNA vez por episodio y se conserva en todos los reintentos."""

    def __init__(self, db_path: Path, clock: Callable[[], float] = time.time):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(OUTBOX_SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock

    def enqueue_pending(self) -> int:
        """Crea filas para los episodios cerrados que están en `cola_sync` y aún no tienen evento."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.episodio_id FROM cola_sync c JOIN episodios e ON e.id = c.episodio_id "
                "LEFT JOIN outbox_v2 o ON o.episodio_id = c.episodio_id WHERE o.episodio_id IS NULL AND e.ts_fin IS NOT NULL"
            ).fetchall()
            for r in rows:
                self._conn.execute("INSERT OR IGNORE INTO outbox_v2(episodio_id, event_id, created_at) VALUES (?,?,?)",
                                   (r["episodio_id"], uuid7(), datetime.now(timezone.utc).isoformat()))
        return len(rows)

    def due(self, limit: int = 10) -> list[OutboxRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT episodio_id, event_id, stage, attempts FROM outbox_v2 WHERE stage != 'done' AND next_ts <= ? "
                "ORDER BY created_at LIMIT ?", (self._clock(), limit)).fetchall()
        return [OutboxRow(**dict(r)) for r in rows]

    def episode(self, episodio_id: str) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM episodios WHERE id=?", (episodio_id,)).fetchone()
        return dict(r) if r else None

    def save_payload(self, event_id: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute("UPDATE outbox_v2 SET payload=? WHERE event_id=?", (json.dumps(payload), event_id))

    def payload(self, event_id: str) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute("SELECT payload FROM outbox_v2 WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(r["payload"]) if r and r["payload"] else None

    def advance(self, event_id: str, stage: str) -> None:
        assert stage in STAGES
        with self._lock:
            self._conn.execute("UPDATE outbox_v2 SET stage=?, attempts=0, next_ts=0, last_error=NULL WHERE event_id=?",
                               (stage, event_id))

    def ack(self, event_id: str, episodio_id: str, snapshot_url: Optional[str]) -> None:
        with self._lock:
            self._conn.execute("UPDATE outbox_v2 SET stage='done', last_error=NULL WHERE event_id=?", (event_id,))
            self._conn.execute("UPDATE episodios SET snapshot_url=? WHERE id=?", (snapshot_url, episodio_id))
            self._conn.execute("DELETE FROM cola_sync WHERE episodio_id=?", (episodio_id,))

    def nack(self, event_id: str, error: str) -> float:
        with self._lock:
            r = self._conn.execute("SELECT attempts FROM outbox_v2 WHERE event_id=?", (event_id,)).fetchone()
            attempts = (r["attempts"] if r else 0) + 1
            delay = float(min(300, 2 ** attempts))
            self._conn.execute("UPDATE outbox_v2 SET attempts=?, next_ts=?, last_error=? WHERE event_id=?",
                               (attempts, self._clock() + delay, error[:300], event_id))
        return delay

    def stats(self) -> dict[str, int]:
        with self._lock:
            r = self._conn.execute("SELECT COUNT(*) AS n FROM outbox_v2 WHERE stage != 'done'").fetchone()
        return {"pending": int(r["n"]), "bytes": 0}

    def close(self) -> None:
        self._conn.close()


# ----------------------------------------------------------------------------- uploader
class Uploader(threading.Thread):
    """Vacía la outbox hacia Cloud Storage (lago de datos) y Firestore. Corre siempre, también en pausa."""

    def __init__(self, outbox: OutboxV2, bucket, events_db, *, edge: dict, is_simulated: bool, tz: str,
                 bucket_name: str, thumbs_dir: Path, interval_s: float = 5.0):
        super().__init__(name="cloud-v2-uploader", daemon=True)
        self.outbox, self.bucket, self.db = outbox, bucket, events_db
        self.edge, self.is_simulated, self.tz, self.bucket_name = edge, is_simulated, tz, bucket_name
        self.thumbs_dir = Path(thumbs_dir); self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval_s
        self._stop = threading.Event()
        self.synced = 0
        self.online = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def tick(self, drain: bool = False) -> int:
        """Procesa los eventos pendientes. `drain=True` (apagado) ignora la señal de parada."""
        n = 0
        try:
            self.outbox.enqueue_pending()
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo leer la cola local: %s", e)
            return 0
        for row in self.outbox.due(10):
            if self._stop.is_set() and not drain:
                break
            try:
                self.process(row)
                n += 1
                self.online = True
            except Exception as e:  # noqa: BLE001
                delay = self.outbox.nack(row.event_id, f"{type(e).__name__}: {e}")
                self.online = False
                log.warning("Episodio %s (%s) en etapa %s falló: %s; reintento en %.0f s",
                            row.episodio_id, row.event_id, row.stage, e, delay)
        return n

    def _payload(self, row: OutboxRow) -> dict:
        p = self.outbox.payload(row.event_id)
        if p:
            return p
        ep = self.outbox.episode(row.episodio_id)
        if ep is None:
            raise RuntimeError("episodio inexistente en la base local")
        thumb = None
        if ep.get("snapshot"):
            thumb = make_thumb(ep["snapshot"], str(self.thumbs_dir / f"{row.event_id}.jpg"))
        p = build_event(ep, row.event_id, edge=self.edge, is_simulated=self.is_simulated, thumb_path=thumb)
        p["_local"] = {"original": ep.get("snapshot_raw"), "thumb": thumb}
        if p["capture"]["status"] == "ok":
            p["capture"]["uri"] = f"gs://{self.bucket_name}/" + lake_path("images", p["device_id"], p["end_ts"], row.event_id, self.tz)
            if thumb:
                p["capture"]["thumb_uri"] = f"gs://{self.bucket_name}/" + lake_path("thumbs", p["device_id"], p["end_ts"], row.event_id, self.tz)
        self.outbox.save_payload(row.event_id, p)
        return p

    def _upload(self, path: str, data: Optional[bytes], content_type: str, metadata: Optional[dict] = None,
                filename: Optional[str] = None) -> None:
        from google.api_core.exceptions import PreconditionFailed  # import tardío
        blob = self.bucket.blob(path)
        if metadata:
            blob.metadata = metadata
        try:
            if filename:
                blob.upload_from_filename(filename, content_type=content_type, if_generation_match=0)
            else:
                blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
        except PreconditionFailed:
            pass  # ya existía: la subida es idempotente

    def process(self, row: OutboxRow) -> None:
        p = self._payload(row)
        local = p.get("_local") or {}
        stage = row.stage
        if stage == "images":
            if p["capture"]["status"] == "ok":
                self._upload(lake_path("images", p["device_id"], p["end_ts"], row.event_id, self.tz), None,
                             "image/jpeg", image_metadata(p, "images"), filename=local["original"])
                if local.get("thumb"):
                    self._upload(lake_path("thumbs", p["device_id"], p["end_ts"], row.event_id, self.tz), None,
                                 "image/jpeg", image_metadata(p, "thumbs"), filename=local["thumb"])
            self.outbox.advance(row.event_id, "sidecar"); stage = "sidecar"
        if stage == "sidecar":
            ev = {k: v for k, v in p.items() if not k.startswith("_")}
            self._upload(lake_path("events", p["device_id"], p["end_ts"], row.event_id, self.tz),
                         (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"), "application/json")
            self.outbox.advance(row.event_id, "firestore"); stage = "firestore"
        if stage == "firestore":
            self._firestore({k: v for k, v in p.items() if not k.startswith("_")})
            self.outbox.ack(row.event_id, row.episodio_id, p["capture"].get("thumb_uri"))
            self.synced += 1
            log.info("Episodio %s publicado como evento %s (%s)", row.episodio_id, row.event_id, p["nombre_clase"])

    def _firestore(self, ev: dict) -> None:
        from google.api_core.exceptions import Conflict  # import tardío
        from google.cloud import firestore
        day = local_day(ev["end_ts"], self.tz)
        batch = self.db.batch()
        batch.create(self.db.collection(EVENTS_COLLECTION).document(ev["event_id"]), to_firestore(ev))
        batch.set(self.db.collection(STATS_COLLECTION).document(f"{ev['device_id']}_{day}"), {
            "device_id": ev["device_id"], "date": day,
            "events": firestore.Increment(1),
            "seconds": firestore.Increment(ev["duration_seconds"]),
            "by_class": {ev["nombre_clase"]: firestore.Increment(1)},
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        try:
            batch.commit(timeout=15)
        except Conflict:   # AlreadyExists: el batch es atómico; ya estaba escrito (y contado)
            pass


# ----------------------------------------------------------------------------- control remoto y salud
class DeviceControl:
    """Lee `device_control/{id}`: desired_state (running/stopped) y lease de video. Conserva el último estado."""

    def __init__(self, control_db, device_id: str, default_state: str = "running", safety_poll_s: float = 300.0,
                 clock: Callable[[], float] = time.monotonic):
        self._doc = control_db.collection(CONTROL_COLLECTION).document(device_id)
        self.default_state = default_state if default_state in ("running", "stopped") else "running"
        self.desired_state = self.default_state
        self.safety_poll_s = safety_poll_s
        self._clock = clock
        self._lock = threading.Lock()
        self._lease_deadline = 0.0
        self._last_renewed = None
        self._capture_requested = False
        self._capture_session_id: Optional[str] = None
        self.last_sync = 0.0
        self._watch = None
        self._stop = threading.Event()

    def apply(self, data: Optional[dict], read_time: Optional[datetime] = None) -> None:
        data = data or {}
        with self._lock:
            d = data.get("desired_state")
            self.desired_state = d if d in ("running", "stopped") else self.default_state
            renewed, lease = data.get("lease_renewed_at"), float(data.get("lease_seconds", 90))
            if not data.get("stream_requested") or renewed is None:
                self._lease_deadline, self._last_renewed = 0.0, None
            elif renewed != self._last_renewed:
                now_wall = read_time or datetime.now(timezone.utc)
                age = max(0.0, (now_wall - parse_ts(renewed)).total_seconds())
                self._lease_deadline = self._clock() + max(0.0, lease - age)
                self._last_renewed = renewed
            self._capture_requested = bool(data.get("capture_requested"))
            self._capture_session_id = data.get("capture_session_id")
            self.last_sync = self._clock()

    def should_run(self) -> bool:
        with self._lock:
            return self.desired_state == "running"

    def should_stream(self) -> bool:
        with self._lock:
            return self.desired_state == "running" and self._clock() < self._lease_deadline

    def should_capture(self) -> bool:
        """Captura de fotos para reentrenamiento: toggle persistente, no depende de un lease."""
        with self._lock:
            return self._capture_requested and self._capture_session_id is not None

    def capture_session(self) -> Optional[str]:
        with self._lock:
            return self._capture_session_id

    def start(self) -> "DeviceControl":
        def on_snapshot(docs, changes, read_time):
            snap = docs[0] if docs else None
            self.apply(snap.to_dict() if snap is not None and snap.exists else None, read_time)
        try:
            self._watch = self._doc.on_snapshot(on_snapshot)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo suscribir a device_control (%s); se usará el sondeo de respaldo", e)
        threading.Thread(target=self._safety_loop, name="cloud-v2-control", daemon=True).start()
        return self

    def _safety_loop(self) -> None:
        while not self._stop.wait(self.safety_poll_s):
            try:
                snap = self._doc.get()
                self.apply(snap.to_dict() if snap.exists else None, getattr(snap, "read_time", None))
            except Exception as e:  # noqa: BLE001
                log.warning("Lectura de respaldo de device_control falló (se conserva el estado): %s", e)

    def close(self) -> None:
        self._stop.set()
        try:
            if self._watch is not None:
                self._watch.unsubscribe()
        except Exception:  # noqa: BLE001
            pass


class HealthReporter(threading.Thread):
    """Escribe `device_health/{id}` cada `heartbeat_s` y ante cambios (antirrebote de 2 s)."""

    def __init__(self, control_db, device_id: str, outbox: OutboxV2, status_fn: Callable[[], dict],
                 edge: dict, heartbeat_s: float = 300.0):
        super().__init__(name="cloud-v2-health", daemon=True)
        self._doc = control_db.collection(HEALTH_COLLECTION).document(device_id)
        self.outbox, self.status_fn, self.edge, self.heartbeat_s = outbox, status_fn, edge, heartbeat_s
        self._dirty, self._halt = threading.Event(), threading.Event()
        self.streaming = False
        self._last_state = None

    def notify(self) -> None:
        self._dirty.set()

    def set_streaming(self, on: bool) -> None:
        if self.streaming != on:
            self.streaming = on
            self._dirty.set()

    def doc(self, reported_state: str) -> dict:
        st = self.status_fn() or {}
        ob = self.outbox.stats()
        return {
            "reported_state": reported_state, "streaming": self.streaming,
            "camera_ok": bool(st.get("camera_ok")), "camera_source": self.edge.get("camera_source"),
            "inference_fps": st.get("fps"), "inference_ms_p50": st.get("epp_ms_p50"),
            "inference_ms_p95": st.get("epp_ms_p95"), "cpu_temp_c": st.get("cpu_temp_c"),
            "outbox_pending": ob["pending"], "outbox_bytes": ob["bytes"],
            "edge_version": self.edge.get("edge_version"), "model_version": self.edge.get("model_version"),
            "fsm_state": st.get("state"),
        }

    def write(self, reported_state: str) -> None:
        from google.cloud import firestore  # import tardío
        d = self.doc(reported_state)
        d["last_seen"] = firestore.SERVER_TIMESTAMP
        try:
            self._doc.set(d, merge=True)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo escribir device_health: %s", e)

    def run(self) -> None:
        self.write(self._reported())
        while not self._halt.is_set():
            woke = self._dirty.wait(self.heartbeat_s)
            if self._halt.is_set():
                break
            if woke:
                self._halt.wait(2.0)
                self._dirty.clear()
            self.write(self._reported())

    def _reported(self) -> str:
        return (self.status_fn() or {}).get("reported_state", "running")

    def final(self) -> None:
        self._halt.set()
        self.write("stopped")


# ----------------------------------------------------------------------------- video bajo demanda
def pack_frame(captured_at_ms: int, jpeg: bytes) -> bytes:
    """Protocolo del relay: uint64 big-endian (ms Unix de captura) + JPEG."""
    return struct.pack(">Q", captured_at_ms) + jpeg


def reencode(jpeg: bytes, width: int, quality: int) -> bytes:
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return jpeg
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else jpeg


class StreamWorker(threading.Thread):
    """Envía el último frame anotado del nodo al relay de Cloud Run solo mientras haya lease vigente."""

    def __init__(self, frames, control: DeviceControl, health: Optional[HealthReporter], url: str, token: str,
                 fps: float = 8.0, width: int = 640, quality: int = 60, connect: Optional[Callable] = None):
        super().__init__(name="cloud-v2-stream", daemon=True)
        self.frames, self.control, self.health = frames, control, health
        self.url, self.token, self.fps, self.width, self.quality = url, token, fps, width, quality
        self._connect = connect
        self._stop = threading.Event()
        self.sent = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        connect = self._connect
        if connect is None:
            import websocket  # websocket-client
            def connect(url, token):
                return websocket.create_connection(url, header=[f"Authorization: Bearer {token}"], timeout=10)
        backoff = 1.0
        while not self._stop.is_set():
            if not self.control.should_stream():
                self._stop.wait(0.25)
                continue
            ws = None
            try:
                ws = connect(self.url, self.token)
                if self.health:
                    self.health.set_streaming(True)
                log.info("Video en vivo conectado al relay")
                backoff, seq, period = 1.0, 0, 1.0 / self.fps
                while self.control.should_stream() and not self._stop.is_set():
                    t0 = time.monotonic()
                    jpg, seq2 = self.frames.wait(seq, timeout=1.0)
                    if jpg is None or seq2 == seq:
                        continue
                    seq = seq2
                    ws.send_binary(pack_frame(int(time.time() * 1000), reencode(jpg, self.width, self.quality)))
                    self.sent += 1
                    self._stop.wait(max(0.0, period - (time.monotonic() - t0)))
                log.info("Video en vivo cerrado (sin espectadores)")
            except Exception as e:  # noqa: BLE001
                log.warning("Video en vivo caído: %s (reintento en %.0f s)", e, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                if self.health:
                    self.health.set_streaming(False)



# ----------------------------------------------------------------------------- emulación local (sin Google Cloud)
class _LocalBlob:
    def __init__(self, root: Path, name: str):
        self.path, self.metadata = root / name, None

    def _guard(self):
        if self.path.exists():
            from google.api_core.exceptions import PreconditionFailed
            raise PreconditionFailed("el objeto ya existe")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def upload_from_filename(self, filename, content_type=None, if_generation_match=None):
        self._guard(); self.path.write_bytes(Path(filename).read_bytes())

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        self._guard(); self.path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


class LocalBucket:
    """Emula un bucket de Cloud Storage en una carpeta (con la misma semántica de solo crear)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def blob(self, name: str) -> _LocalBlob:
        return _LocalBlob(self.root, name)


class _LocalDoc:
    def __init__(self, root: Path, coll: str, doc_id: str):
        self.path = root / coll / f"{doc_id}.json"

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def set(self, data: dict, merge: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cur = self._read() if merge else {}
        _merge_local(cur, data)
        self.path.write_text(json.dumps(cur, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def get(self):
        d = self._read()
        return type("Snap", (), {"exists": bool(d), "to_dict": lambda _s: d, "read_time": datetime.now(timezone.utc)})()

    def on_snapshot(self, cb):
        cb([self.get()], [], datetime.now(timezone.utc))
        return type("W", (), {"unsubscribe": lambda _s: None})()


def _merge_local(cur: dict, data: dict) -> None:
    from google.cloud import firestore
    for k, v in data.items():
        if isinstance(v, dict):
            sub = cur.setdefault(k, {})
            _merge_local(sub, v)
        elif isinstance(v, firestore.Increment):
            cur[k] = cur.get(k, 0) + v.value
        elif v is firestore.SERVER_TIMESTAMP:
            cur[k] = datetime.now(timezone.utc).isoformat()
        else:
            cur[k] = v


class _LocalBatch:
    def __init__(self):
        self.ops = []

    def create(self, ref, data):
        self.ops.append(("create", ref, data))

    def set(self, ref, data, merge=False):
        self.ops.append(("set", ref, data, merge))

    def commit(self, timeout=None):
        from google.api_core.exceptions import Conflict
        for op in self.ops:
            if op[0] == "create" and op[1].path.exists():
                raise Conflict("el documento ya existe")      # batch atómico: no se aplica nada
        for op in self.ops:
            if op[0] == "create":
                op[1].set(op[2])
            else:
                op[1].set(op[2], merge=op[3])


class LocalFirestore:
    """Emula una base de Firestore en una carpeta: una subcarpeta por colección y un JSON por documento."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def collection(self, name: str):
        root = self.root
        return type("C", (), {"document": lambda _s, doc_id: _LocalDoc(root, name, doc_id)})()

    def batch(self) -> _LocalBatch:
        return _LocalBatch()

# ----------------------------------------------------------------------------- arranque
def read_secret(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def start_cloud_v2(cfg: dict, base_dir: Path, db_path: Path, frames, status_fn: Callable[[], dict],
                   is_simulated: bool, cam=None) -> dict:
    """Crea los clientes de Google y arranca outbox, uploader, control, salud, video y captura. Devuelve los componentes."""
    c = cfg["cloud_v2"]
    cred = (base_dir / c["credentials_file"]).resolve()
    if cred.exists():
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(cred))
    project = c["project_id"]
    if c.get("emulate_dir"):          # prueba sin Google Cloud: lago y Firestore en una carpeta local
        root = (base_dir / c["emulate_dir"]).resolve()
        events_db = LocalFirestore(root / "firestore" / c.get("detections_database", "dbraspberry"))
        control_db = LocalFirestore(root / "firestore" / "default")
        bucket = LocalBucket(root / "gcs" / c["lake_bucket"])
        log.warning("Nube 2.0.0 EMULADA en %s (no se envía nada a Google Cloud)", root)
    else:
        from google.cloud import firestore, storage  # import tardío
        events_db = firestore.Client(project=project, database=c.get("detections_database", "dbraspberry"))
        control_db = firestore.Client(project=project, database=c.get("control_database", "(default)"))
        bucket = storage.Client(project=project).bucket(c["lake_bucket"])
    edge = {"edge_version": EDGE_VERSION,
            "model_version": model_version((base_dir / cfg["epp"]["model"]).resolve()),
            "camera_source": "file" if is_simulated else "usb"}
    outbox = OutboxV2(db_path)
    uploader = Uploader(outbox, bucket, events_db, edge=edge, is_simulated=is_simulated,
                        tz=c.get("site_timezone", "America/Lima"), bucket_name=c["lake_bucket"],
                        thumbs_dir=(base_dir / cfg["storage"]["snapshots_dir"] / "thumbs").resolve(),
                        interval_s=c.get("sync_interval_s", 5))
    uploader.start()
    control = DeviceControl(control_db, cfg["device"]["id"], c.get("default_desired_state", "running"),
                            c.get("control_safety_poll_s", 300)).start()
    health = HealthReporter(control_db, cfg["device"]["id"], outbox, status_fn, edge, c.get("heartbeat_s", 300))
    health.start()
    streamer = None
    if c.get("live_stream", True) and not c.get("emulate_dir"):
        token = read_secret((base_dir / c["device_token_file"]).resolve())
        url = f"wss://{c['orchestrator_host']}/ws/video/{cfg['device']['id']}"
        streamer = StreamWorker(frames, control, health, url, token, c.get("stream_fps", 8),
                                c.get("stream_width", 640), c.get("stream_jpeg_quality", 60))
        streamer.start()
    capture_worker = None
    if cam is not None:
        from .training_capture import CaptureWorker  # import tardío: módulo propio, sin ciclo con cloud_v2
        tc = c.get("training_capture", {})
        capture_worker = CaptureWorker(cam, control, bucket, events_db, cfg["device"]["id"],
                                       fps=tc.get("fps", 1.0), jpeg_quality=tc.get("jpeg_quality", 85),
                                       max_duration_s=tc.get("max_duration_s", 3600))
        capture_worker.start()
    log.info("Nube 2.0.0 activa: proyecto %s, bucket %s, host %s", project, c["lake_bucket"], c.get("orchestrator_host"))
    return {"outbox": outbox, "uploader": uploader, "control": control, "health": health, "streamer": streamer,
            "capture": capture_worker}
