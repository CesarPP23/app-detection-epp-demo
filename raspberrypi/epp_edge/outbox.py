"""Outbox local (SQLite) y Uploader idempotente hacia el data lake y Firestore (2.9).

Orden de subida por evento: imágenes -> sidecar JSON -> Firestore. Así, si el evento existe en
Firestore, su captura ya está en el lake. `stage` avanza solo cuando el paso termina, de modo
que un reinicio retoma donde quedó. Los objetos se crean con `if_generation_match=0` (la SA de
la Pi solo puede crear): `PreconditionFailed` significa "ya existía" y se trata como éxito.
"""
import json
import logging
import shutil
import sqlite3
import threading
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.api_core.exceptions import Conflict, PreconditionFailed

EVENTS_COLLECTION = "detecciones_confirmadas"
STATS_COLLECTION = "daily_stats"
STAGES = ("images", "sidecar", "firestore")

OutboxRow = namedtuple("OutboxRow", "event_id payload pending_dir stage attempts")


# --- helpers de contrato -------------------------------------------------------------------------


def iso(o: Any) -> str:
    """`default` de json.dumps: datetimes en UTC con milisegundos y sufijo Z."""
    if isinstance(o, datetime):
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"no serializable: {type(o).__name__}")


def parse_ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def local_day(p: dict, tz: str = "America/Lima") -> str:
    """Fecha local (SITE_TIMEZONE) de `end_ts`, formato YYYY-MM-DD."""
    return parse_ts(p["end_ts"]).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def lake_prefix(p: dict, tz: str = "America/Lima") -> str:
    return f"device={p['device_id']}/dt={local_day(p, tz)}"


def to_firestore(p: dict) -> dict:
    """Mismo contrato, con Timestamps nativos y `created_at` del servidor."""
    from google.cloud import firestore  # import tardío

    doc = json.loads(json.dumps(p, default=iso))
    doc["start_ts"] = parse_ts(doc["start_ts"])
    doc["end_ts"] = parse_ts(doc["end_ts"])
    cap = doc.get("capture")
    if isinstance(cap, dict) and cap.get("captured_at"):
        cap["captured_at"] = parse_ts(cap["captured_at"])
    doc["created_at"] = firestore.SERVER_TIMESTAMP
    return doc


def image_metadata(p: dict, kind: str) -> dict[str, str]:
    """Metadata `x-goog-meta-*` de una imagen: se describe sola aunque se pierda el resto.
    `kind` es el prefijo del lake (`images` | `thumbs`)."""
    cap = p.get("capture") or {}
    is_thumb = kind == "thumbs"
    meta = {
        "event_id": p["event_id"],
        "device_id": p["device_id"],
        "nombre_clase": p["nombre_clase"],
        "confianza_max": p["confianza_max"],
        "model_version": (p.get("edge") or {}).get("model_version"),
        "captured_at": cap.get("captured_at"),
        "sha256": cap.get("thumb_sha256" if is_thumb else "sha256"),
        "kind": "thumb" if is_thumb else "original",
    }
    return {k: str(v) for k, v in meta.items() if v is not None}


def _dir_size(path: Path | str | None) -> int:
    if not path:
        return 0
    total = 0
    try:
        for f in Path(path).iterdir():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


# --- Outbox --------------------------------------------------------------------------------------


class Outbox:
    def __init__(self, path: Path | str, s: Any, clock=time.time, log: logging.Logger | None = None) -> None:
        self.path = Path(path)
        self.s = s
        self._clock = clock
        self.log = log or logging.getLogger("outbox")
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # autocommit: cada sentencia es atómica; la migración usa BEGIN/COMMIT explícitos
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS outbox (
                event_id        TEXT PRIMARY KEY,
                payload         TEXT NOT NULL,
                pending_dir     TEXT,
                stage           TEXT NOT NULL DEFAULT 'images',
                created_at      REAL NOT NULL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                last_error      TEXT
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS outbox_due ON outbox(next_attempt_at)")
        self._migrate_legacy()
        self._cleanup_orphans()

    # -- escritura --

    def put(self, payload: dict, pending_dir: Path | str | None) -> bool:
        """Encola un evento. Idempotente por `event_id`. Devuelve True si era nuevo."""
        data = json.dumps(payload, separators=(",", ":"), default=iso)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO outbox (event_id, payload, pending_dir, stage, created_at) "
                "VALUES (?,?,?,?,?)",
                (payload["event_id"], data, str(pending_dir) if pending_dir else None, "images", self._clock()),
            )
            inserted = cur.rowcount == 1
        self.enforce_cap()
        return inserted

    def advance(self, event_id: str, stage: str) -> None:
        """Marca el paso terminado. Al salir de `images` las imágenes locales ya no hacen falta."""
        if stage not in STAGES:
            raise ValueError(f"stage inválido: {stage}")
        with self._lock:
            row = self._conn.execute("SELECT pending_dir FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                return
            if stage != "images" and row[0]:
                shutil.rmtree(row[0], ignore_errors=True)
                self._conn.execute(
                    "UPDATE outbox SET stage=?, pending_dir=NULL, attempts=0, last_error=NULL WHERE event_id=?",
                    (stage, event_id),
                )
            else:
                self._conn.execute(
                    "UPDATE outbox SET stage=?, attempts=0, last_error=NULL WHERE event_id=?", (stage, event_id)
                )

    def ack(self, event_id: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT pending_dir FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            self._conn.execute("DELETE FROM outbox WHERE event_id=?", (event_id,))
        if row and row[0]:
            shutil.rmtree(row[0], ignore_errors=True)

    def nack(self, event_id: str, error: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT attempts FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                return
            attempts = row[0] + 1
            self._conn.execute(
                "UPDATE outbox SET attempts=?, next_attempt_at=?, last_error=? WHERE event_id=?",
                (attempts, self._clock() + min(300, 2**attempts), error[:500], event_id),
            )

    def mark_capture_lost(self, event_id: str, status: str, reason: str = "") -> None:
        """La captura ya no existe en disco: el evento sigue su camino sin imagen (nunca se descarta)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, pending_dir FROM outbox WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                return
            self._set_capture_status(event_id, json.loads(row[0]), status, reason)
            if row[1]:
                shutil.rmtree(row[1], ignore_errors=True)

    def _set_capture_status(self, event_id: str, p: dict, status: str, reason: str) -> None:
        cap = p.get("capture") or {}
        cap.update({"status": status, "uri": None, "thumb_uri": None})
        if reason:
            cap["reason"] = reason
        p["capture"] = cap
        self._conn.execute(
            "UPDATE outbox SET payload=?, pending_dir=NULL, stage=CASE WHEN stage='images' THEN 'sidecar' "
            "ELSE stage END WHERE event_id=?",
            (json.dumps(p, separators=(",", ":"), default=iso), event_id),
        )

    # -- lectura --

    def due(self, limit: int = 10) -> list[OutboxRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, payload, pending_dir, stage, attempts FROM outbox "
                "WHERE next_attempt_at <= ? ORDER BY created_at LIMIT ?",
                (self._clock(), limit),
            ).fetchall()
        return [OutboxRow(*r) for r in rows]

    def get_payload(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM outbox WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def stats(self) -> dict[str, int]:
        """{'pending': filas, 'bytes': tamaño de capturas pendientes} para device_health."""
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            dirs = [r[0] for r in self._conn.execute("SELECT pending_dir FROM outbox WHERE pending_dir IS NOT NULL")]
        return {"pending": n, "bytes": sum(_dir_size(d) for d in dirs)}

    def stages(self) -> dict[str, int]:
        with self._lock:
            return dict(self._conn.execute("SELECT stage, COUNT(*) FROM outbox GROUP BY 1").fetchall())

    # -- mantenimiento --

    def enforce_cap(self) -> int:
        """Si las capturas pendientes superan OUTBOX_MAX_BYTES, borra las más viejas (solo las
        imágenes, no los eventos) y las marca `dropped_disk_full`. Devuelve cuántas borró."""
        cap = self.s.outbox_max_bytes
        dropped = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, payload, pending_dir, stage FROM outbox "
                "WHERE pending_dir IS NOT NULL ORDER BY created_at"
            ).fetchall()
            sizes = {r[0]: _dir_size(r[2]) for r in rows}
            total = sum(sizes.values())
            for event_id, payload, pending_dir, stage in rows:
                if total <= cap:
                    break
                if stage != "images":
                    continue
                total -= sizes[event_id]
                shutil.rmtree(pending_dir, ignore_errors=True)
                self._set_capture_status(event_id, json.loads(payload), "dropped_disk_full", "outbox_max_bytes")
                dropped += 1
                self.log.critical(
                    "Outbox por encima de OUTBOX_MAX_BYTES: se descartó la captura de %s (el evento se conserva)",
                    event_id,
                )
        return dropped

    def _cleanup_orphans(self) -> None:
        """Directorios de capturas que ningún evento del outbox referencia (p. ej. corte de luz
        entre build_capture y put) y archivos .tmp a medio escribir."""
        pending = Path(getattr(self.s, "data_dir", self.path.parent)) / "pending"
        if not pending.is_dir():
            return
        with self._lock:
            known = {r[0] for r in self._conn.execute("SELECT pending_dir FROM outbox WHERE pending_dir IS NOT NULL")}
        for d in pending.iterdir():
            if d.is_dir() and str(d) not in known:
                self.log.warning("Captura huérfana eliminada: %s", d.name)
                shutil.rmtree(d, ignore_errors=True)
            elif d.is_dir():
                for tmp in d.glob("*.tmp"):
                    tmp.unlink(missing_ok=True)

    def _migrate_legacy(self) -> None:
        """Importa la tabla vieja `offline_detections` (sin captura) y la elimina."""
        has = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='offline_detections'"
        ).fetchone()
        if not has:
            return
        from .ids import uuid7

        rows = self._conn.execute("SELECT id, payload, created_at FROM offline_detections ORDER BY id").fetchall()
        self._conn.execute("BEGIN")
        try:
            for _id, raw, created in rows:
                try:
                    p = json.loads(raw)
                except ValueError:
                    continue
                p = self._normalize_legacy(p, created, uuid7())
                self._conn.execute(
                    "INSERT OR IGNORE INTO outbox (event_id, payload, pending_dir, stage, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (p["event_id"], json.dumps(p, separators=(",", ":"), default=iso), None, "firestore", self._clock()),
                )
            self._conn.execute("DROP TABLE offline_detections")
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        self.log.info("Migradas %d detecciones de offline_detections al outbox", len(rows))

    def _normalize_legacy(self, p: dict, created: str | None, event_id: str) -> dict:
        p = dict(p)
        p["schema_version"] = 2
        p["event_id"] = event_id
        p.setdefault("event_type", "deteccion_confirmada")
        p.setdefault("device_id", getattr(self.s, "device_id", "unknown"))
        p.setdefault("is_confirmed", True)
        p.setdefault("closed_reason", "legacy")
        p.setdefault("is_simulated", False)
        end = p.get("end_ts") or p.get("end_ts_iso") or p.get("timestamp") or created
        start = p.get("start_ts") or p.get("start_ts_iso") or end
        p["end_ts"], p["start_ts"] = parse_ts(end), parse_ts(start)
        p.setdefault("duration_seconds", max(0.0, (p["end_ts"] - p["start_ts"]).total_seconds()))
        p["capture"] = {"status": "missing_legacy", "uri": None, "thumb_uri": None}
        return p

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- Uploader ------------------------------------------------------------------------------------


class Uploader(threading.Thread):
    """Hilo que corre siempre (también con la detección pausada) y vacía el outbox."""

    def __init__(self, outbox: Outbox, events_db, bucket, s, stop: threading.Event, log: logging.Logger) -> None:
        super().__init__(name="uploader", daemon=True)
        self.outbox, self.bucket, self.s, self.stop_event, self.log = outbox, bucket, s, stop, log
        self.events_db = events_db
        self.events = events_db.collection(EVENTS_COLLECTION)
        self.stats = events_db.collection(STATS_COLLECTION)
        self._tz = getattr(s, "site_timezone", "America/Lima")

    def run(self) -> None:
        while not self.stop_event.is_set():
            worked = False
            for row in self.outbox.due(10):
                if self.stop_event.is_set():
                    break
                worked |= self._safe(row)
            self.stop_event.wait(1.0 if worked else 5.0)

    def drain(self, timeout: float = 10.0) -> None:
        """Apagado: deja que el hilo termine y procesa lo pendiente durante `timeout` segundos."""
        deadline = time.monotonic() + timeout
        if self.is_alive():
            self.join(timeout=max(0.0, deadline - time.monotonic()))
        while time.monotonic() < deadline:
            rows = self.outbox.due(10)
            if not rows:
                break
            if not any([self._safe(r) for r in rows]):
                break

    def _safe(self, row: OutboxRow) -> bool:
        try:
            self._process(row)
            return True
        except Exception as e:  # noqa: BLE001 - cualquier fallo se reintenta con backoff
            self.outbox.nack(row.event_id, f"{type(e).__name__}: {e}")
            self.log.warning("Subida de %s falló en '%s' (intento %d): %s", row.event_id, row.stage, row.attempts + 1, e)
            return False

    def _process(self, row: OutboxRow) -> None:
        p = json.loads(row.payload)
        eid = p["event_id"]
        prefix = lake_prefix(p, self._tz)
        stage = row.stage
        if stage == "images":
            if row.pending_dir:
                try:
                    for name, kind in (("original.jpg", "images"), ("thumb.jpg", "thumbs")):
                        blob = self.bucket.blob(f"raw/{kind}/{prefix}/{eid}.jpg")
                        blob.metadata = image_metadata(p, kind)
                        try:
                            blob.upload_from_filename(
                                str(Path(row.pending_dir) / name),
                                content_type="image/jpeg",
                                if_generation_match=0,
                                timeout=60,
                            )
                        except PreconditionFailed:
                            pass  # ya existía: idempotente
                except FileNotFoundError:
                    self.log.error("Faltan archivos locales de la captura de %s; se registra sin imagen", eid)
                    self.outbox.mark_capture_lost(eid, "missing", "local_files_missing")
                    p = self.outbox.get_payload(eid) or p
            self.outbox.advance(eid, "sidecar")
            stage = "sidecar"
        if stage == "sidecar":
            blob = self.bucket.blob(f"raw/events/{prefix}/{eid}.json")
            try:
                blob.upload_from_string(
                    json.dumps(p, separators=(",", ":"), default=iso),  # UNA línea (NDJSON)
                    content_type="application/json",
                    if_generation_match=0,
                    timeout=30,
                )
            except PreconditionFailed:
                pass
            self.outbox.advance(eid, "firestore")
        self._firestore(p)
        self.outbox.ack(eid)

    def _firestore(self, p: dict) -> None:
        from google.cloud import firestore  # import tardío

        day = local_day(p, self._tz)
        batch = self.events_db.batch()
        batch.create(self.events.document(p["event_id"]), to_firestore(p))
        batch.set(
            self.stats.document(f"{p['device_id']}_{day}"),
            {
                "device_id": p["device_id"],
                "date": day,
                "events": firestore.Increment(1),
                "seconds": firestore.Increment(p["duration_seconds"]),
                "by_class": {p["nombre_clase"]: firestore.Increment(1)},
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        try:
            batch.commit(timeout=15)
        except Conflict:  # AlreadyExists: el batch es atómico, ya estaba escrito (y contado)
            pass
