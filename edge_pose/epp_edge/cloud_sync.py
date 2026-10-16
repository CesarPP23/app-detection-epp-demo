"""Sincronización con la nube (Cloud Run: epp-api).

Dos canales independientes, cada uno en su propio hilo:

  1. EpisodeSyncer  — vacía la cola SQLite: POST /v1/episodes (JSON) y, si corresponde,
                      POST /v1/episodes/{id}/snapshot (JPEG). Reintentos con backoff.
                      Además envía un heartbeat POST /v1/devices/{id}/heartbeat cada ciclo.
  2. LiveStreamer   — envía frames anotados por WebSocket wss://.../v1/live/{device}
                      (solo mientras la FSM esté en ACTIVE). Si el WS falla, cae a
                      POST /v1/live/{device}/frame. El relay en la nube los sirve como MJPEG.

Autenticación: cabecera X-API-Key (o ?key= en el WS).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

import requests

from .local_server import FrameBuffer, cpu_temperature
from .storage import Storage

log = logging.getLogger("epp.cloud")


def load_api_key(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log.warning("No existe %s; la sincronización con la nube fallará (401)", path)
        return ""


class EpisodeSyncer(threading.Thread):
    def __init__(self, cfg: dict, storage: Storage, base_dir: Path, status_fn: Callable[[], dict]):
        super().__init__(name="cloud-sync", daemon=True)
        c = cfg["cloud"]
        self.api = c["api_url"].rstrip("/")
        self.key = load_api_key((base_dir / c["api_key_file"]).resolve())
        self.interval = c["sync_interval_s"]
        self.timeout = c["request_timeout_s"]
        self.upload_snapshots = c["upload_snapshots"]
        self.storage = storage
        self.device_id = cfg["device"]["id"]
        self.status_fn = status_fn
        self._stop = threading.Event()
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": self.key, "User-Agent": "epp-edge/1.0"})
        self.online = False
        self.last_ok = 0.0
        self.synced = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._heartbeat()
                self._drain()
            except Exception as e:  # nunca dejar morir el hilo
                log.debug("sync loop: %s", e)
            self._stop.wait(self.interval)

    # ------------------------------------------------------------------ http
    def _heartbeat(self) -> None:
        st = self.status_fn()
        st["cpu_temp_c"] = cpu_temperature()
        st["pending"] = self.storage.pending_count()
        st["stats_today"] = self.storage.stats_today()
        try:
            r = self._session.post(f"{self.api}/v1/devices/{self.device_id}/heartbeat", json=st, timeout=self.timeout)
            self.online = r.status_code < 500
            if r.status_code == 401:
                log.error("API key rechazada por la nube (401)")
            if self.online:
                self.last_ok = time.time()
        except requests.RequestException as e:
            if self.online:
                log.warning("Nube inaccesible: %s", e.__class__.__name__)
            self.online = False

    def _drain(self) -> None:
        if not self.online:
            return
        for ep in self.storage.pending(limit=20):
            ep_id = ep["id"]
            try:
                payload = {k: v for k, v in ep.items() if k not in ("intentos", "snapshot", "clip", "creado_en")}
                for k, default in (("faltantes", []), ("mal_colocados", []), ("no_evaluables", []), ("personas", [])):
                    v = ep.get(k)
                    payload[k] = json.loads(v) if isinstance(v, str) and v else (v or default)
                payload["muestras"] = self.storage.get_samples(ep_id)
                r = self._session.post(f"{self.api}/v1/episodes", json=payload, timeout=self.timeout)
                if r.status_code not in (200, 201, 409):
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                snapshot_url = None
                snap = ep.get("snapshot")
                if self.upload_snapshots and snap and Path(snap).exists():
                    with open(snap, "rb") as f:
                        r2 = self._session.post(f"{self.api}/v1/episodes/{ep_id}/snapshot",
                                                files={"file": (f"{ep_id}.jpg", f, "image/jpeg")}, timeout=self.timeout * 3)
                    if r2.status_code in (200, 201):
                        snapshot_url = r2.json().get("url")
                    else:
                        raise RuntimeError(f"snapshot HTTP {r2.status_code}")
                self.storage.mark_synced(ep_id, snapshot_url)
                self.synced += 1
                log.info("Episodio %s sincronizado", ep_id)
            except Exception as e:
                self.storage.mark_failed(ep_id, str(e))
                log.warning("Fallo sync %s: %s", ep_id, e)
                if isinstance(e, requests.RequestException):
                    self.online = False
                    return


class LiveStreamer(threading.Thread):
    """Empuja frames del FrameBuffer a la nube mientras el estado sea ACTIVE."""

    def __init__(self, cfg: dict, frames: FrameBuffer, base_dir: Path, state_fn: Callable[[], str]):
        super().__init__(name="live-stream", daemon=True)
        c = cfg["cloud"]
        self.api = c["api_url"].rstrip("/")
        self.ws_url = self.api.replace("https://", "wss://").replace("http://", "ws://")
        self.key = load_api_key((base_dir / c["api_key_file"]).resolve())
        self.period = 1.0 / c["live_fps"]
        self.frames = frames
        self.device_id = cfg["device"]["id"]
        self.state_fn = state_fn
        self.timeout = c["request_timeout_s"]
        self._stop = threading.Event()
        self._ws = None
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": self.key, "Content-Type": "image/jpeg"})
        self.sent = 0
        self.mode = "idle"

    def stop(self) -> None:
        self._stop.set()
        self._close_ws()

    def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _connect_ws(self) -> bool:
        try:
            import websocket  # websocket-client
            self._ws = websocket.create_connection(
                f"{self.ws_url}/v1/live/{self.device_id}?key={self.key}", timeout=self.timeout,
                header=["User-Agent: epp-edge/1.0"])
            self.mode = "ws"
            log.info("WebSocket de video en vivo conectado")
            return True
        except Exception as e:
            log.debug("WS no disponible (%s); usando HTTP POST", e.__class__.__name__)
            self._ws = None
            self.mode = "http"
            return False

    def run(self) -> None:
        seq = -1
        idle_since = None
        while not self._stop.is_set():
            if self.state_fn() != "ACTIVE":
                # sin persona: cerrar canal tras 10 s de inactividad para no gastar red
                if self._ws is not None:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 10:
                        self._close_ws(); self.mode = "idle"
                time.sleep(0.2)
                continue
            idle_since = None
            jpg, seq_new = self.frames.wait(seq, timeout=1.0)
            if jpg is None or seq_new == seq:
                continue
            seq = seq_new
            t0 = time.time()
            self._send(jpg)
            time.sleep(max(0.0, self.period - (time.time() - t0)))

    def _send(self, jpg: bytes) -> None:
        if self._ws is None and self.mode != "http":
            self._connect_ws()
        if self._ws is not None:
            try:
                self._ws.send_binary(jpg)
                self.sent += 1
                return
            except Exception:
                self._close_ws()
        try:
            r = self._session.post(f"{self.api}/v1/live/{self.device_id}/frame", data=jpg, timeout=self.timeout)
            if r.status_code < 300:
                self.sent += 1
            self.mode = "http"
        except requests.RequestException:
            time.sleep(2)
            self.mode = "idle"  # forzará reintento de WS en el siguiente envío
