"""Almacenamiento local (SQLite) con cola de sincronización a la nube (store-and-forward).

Tablas:
  episodios   : una fila por presencia continua de una persona en la zona.
  muestras    : detecciones por frame muestreadas cada N segundos dentro de un episodio.
  cola_sync   : referencias a episodios pendientes de subir (con reintentos y backoff).

El Pi puede funcionar sin internet indefinidamente; cuando vuelve la conexión se vacía la cola.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("epp.storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodios (
    id            TEXT PRIMARY KEY,          -- <device>-<ts_inicio_ms>
    device_id     TEXT NOT NULL,
    zona          TEXT,
    ts_inicio     TEXT NOT NULL,             -- ISO 8601 UTC
    ts_fin        TEXT,
    duracion_s    REAL,
    frames        INTEGER DEFAULT 0,
    personas_max  INTEGER DEFAULT 1,
    ratio_cofia   REAL, ratio_guante REAL, ratio_mascarilla REAL,
    conf_cofia    REAL, conf_guante  REAL, conf_mascarilla  REAL,
    cumple        INTEGER,                   -- 1 si todos los EPP requeridos superan el umbral
    faltantes     TEXT,                      -- JSON lista de EPP no detectados
    snapshot      TEXT,                      -- ruta local del JPEG anotado (mejor frame)
    snapshot_url  TEXT,                      -- URL en Cloud Storage tras subir
    epp_fps       REAL,                      -- fps promedio de inferencia EPP en el episodio
    mal_colocados TEXT,                      -- JSON lista "EPP: motivo" (validación por pose)
    no_evaluables TEXT,                      -- JSON lista de EPP sin frames evaluables
    n_personas    INTEGER,
    personas      TEXT,                      -- JSON: estadísticas por persona (track id)
    clip          TEXT,                      -- ruta local del MP4 de evidencia (opcional)
    creado_en     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS muestras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episodio_id TEXT NOT NULL REFERENCES episodios(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    cofia       INTEGER, guante INTEGER, mascarilla INTEGER,   -- conteos en el frame
    personas    INTEGER,
    detecciones TEXT,                                           -- JSON [[label,conf,x1,y1,x2,y2],...]
    estados     TEXT                                            -- JSON {"P1": {"Cofia":"ok",...}}
);
CREATE TABLE IF NOT EXISTS cola_sync (
    episodio_id TEXT PRIMARY KEY REFERENCES episodios(id) ON DELETE CASCADE,
    intentos    INTEGER DEFAULT 0,
    proximo_ts  REAL DEFAULT 0,
    ultimo_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_inicio ON episodios(ts_inicio);
CREATE INDEX IF NOT EXISTS idx_muestras_ep ON muestras(episodio_id);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        log.info("SQLite listo en %s", db_path)

    def _migrate(self) -> None:
        """Añade columnas nuevas a bases creadas con versiones anteriores."""
        want = {"episodios": {"mal_colocados": "TEXT", "no_evaluables": "TEXT", "n_personas": "INTEGER",
                              "personas": "TEXT", "clip": "TEXT",
                              # nube 2.0.0: frame original del mejor momento, sus cajas y confianzas del episodio
                              "snapshot_raw": "TEXT", "best_dets": "TEXT", "best_ts": "TEXT",
                              "conf_mean": "REAL", "conf_max": "REAL", "closed_reason": "TEXT",
                              # modelo nuevo: la clase "Gorra" pasó a llamarse "Cofia" (bases ya creadas conservan
                              # ratio_gorra/conf_gorra sin usar; no se borran, SQLite no soporta DROP COLUMN fácil)
                              "ratio_cofia": "REAL", "conf_cofia": "REAL"},
                "muestras": {"estados": "TEXT", "cofia": "INTEGER"}}
        for table, cols in want.items():
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for c, t in cols.items():
                if c not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")

    # -------------------------------------------------------------- episodios
    def open_episode(self, episode_id: str, device_id: str, zona: str, ts_inicio: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO episodios(id, device_id, zona, ts_inicio, creado_en) VALUES (?,?,?,?,?)",
                (episode_id, device_id, zona, ts_inicio, utc_now_iso()),
            )

    def add_sample(self, episode_id: str, ts: str, counts: dict[str, int], personas: int, dets: list,
                   estados: Optional[dict] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO muestras(episodio_id, ts, cofia, guante, mascarilla, personas, detecciones, estados) VALUES (?,?,?,?,?,?,?,?)",
                (episode_id, ts, counts.get("Cofia", 0), counts.get("Guante", 0), counts.get("Mascarilla", 0),
                 personas, json.dumps(dets), json.dumps(estados or {}, ensure_ascii=False)),
            )

    def close_episode(self, episode_id: str, data: dict[str, Any]) -> None:
        cols = ["ts_fin", "duracion_s", "frames", "personas_max", "ratio_cofia", "ratio_guante", "ratio_mascarilla",
                "conf_cofia", "conf_guante", "conf_mascarilla", "cumple", "faltantes", "snapshot", "epp_fps",
                "mal_colocados", "no_evaluables", "n_personas", "personas", "clip",
                "snapshot_raw", "best_dets", "best_ts", "conf_mean", "conf_max", "closed_reason"]
        sets = ", ".join(f"{c}=?" for c in cols)
        vals = [data.get(c) for c in cols]
        for c in ("faltantes", "mal_colocados", "no_evaluables", "personas", "best_dets"):
            v = vals[cols.index(c)]
            if isinstance(v, (list, dict)):
                vals[cols.index(c)] = json.dumps(v, ensure_ascii=False)
        with self._lock:
            self._conn.execute(f"UPDATE episodios SET {sets} WHERE id=?", (*vals, episode_id))
            self._conn.execute("INSERT OR IGNORE INTO cola_sync(episodio_id) VALUES (?)", (episode_id,))

    def get_episode(self, episode_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM episodios WHERE id=?", (episode_id,)).fetchone()
        return dict(row) if row else None

    def get_samples(self, episode_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT ts, cofia, guante, mascarilla, personas, estados FROM muestras WHERE episodio_id=? ORDER BY ts",
                                      (episode_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["estados"] = json.loads(d["estados"]) if d.get("estados") else {}
            except Exception:
                d["estados"] = {}
            out.append(d)
        return out

    def recent_episodes(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM episodios ORDER BY ts_inicio DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats_today(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n, SUM(cumple) ok, AVG(duracion_s) dur FROM episodios "
                "WHERE ts_fin IS NOT NULL AND date(ts_inicio) = date('now')").fetchone()
        n = row["n"] or 0
        return {"episodios": n, "cumplen": row["ok"] or 0, "duracion_media_s": round(row["dur"] or 0, 1),
                "tasa_cumplimiento": round((row["ok"] or 0) / n, 3) if n else None}

    # --------------------------------------------------------------- cola sync
    def pending(self, limit: int = 20) -> list[dict]:
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.*, q.intentos FROM cola_sync q JOIN episodios e ON e.id=q.episodio_id "
                "WHERE q.proximo_ts <= ? AND e.ts_fin IS NOT NULL ORDER BY e.ts_inicio LIMIT ?", (now, limit)).fetchall()
        return [dict(r) for r in rows]

    def pending_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM cola_sync").fetchone()[0]

    def mark_synced(self, episode_id: str, snapshot_url: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cola_sync WHERE episodio_id=?", (episode_id,))
            if snapshot_url:
                self._conn.execute("UPDATE episodios SET snapshot_url=? WHERE id=?", (snapshot_url, episode_id))

    def mark_failed(self, episode_id: str, error: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT intentos FROM cola_sync WHERE episodio_id=?", (episode_id,)).fetchone()
            n = (row["intentos"] if row else 0) + 1
            backoff = min(600, 5 * (2 ** min(n, 7)))  # 10 s ... 10 min
            self._conn.execute("UPDATE cola_sync SET intentos=?, proximo_ts=?, ultimo_error=? WHERE episodio_id=?",
                               (n, time.time() + backoff, error[:300], episode_id))

    # ------------------------------------------------------------ mantenimiento
    def purge_old_snapshots(self, snapshots_dir: Path, keep_days: int) -> int:
        cutoff = time.time() - keep_days * 86400
        n = 0
        for p in snapshots_dir.glob("*.jpg"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                n += 1
        return n

    def close(self) -> None:
        with self._lock:
            self._conn.close()
