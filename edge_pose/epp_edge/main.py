"""Orquestador del nodo de borde.

    python -m epp_edge.main                 # usa epp_edge/config.yaml
    python -m epp_edge.main --config otro.yaml --source video.mp4 --no-cloud

Hilos:
  camera        captura continua (último frame disponible)
  main          máquina de estados (IDLE / PERSON_CHECK / ACTIVE)
  local-http    stream MJPEG + estado en LAN
  cloud-v2-*    nube 2.0.0 (por defecto): outbox -> lago de datos (Cloud Storage) + Firestore, control remoto,
                salud y video bajo demanda (ver cloud_v2.py)
  cloud-sync    nube del prototipo (modo legado, `cloud.enabled`): API epp-api
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path

import yaml

from .alerts import Alerter
from .capture import Camera
from .cloud_sync import EpisodeSyncer, LiveStreamer
from .local_server import cpu_temperature
from .local_server import FrameBuffer, LocalServer
from .state_machine import EPPStateMachine
from .storage import Storage

BASE_DIR = Path(__file__).resolve().parent


class SystemdNotifier:
    """Envía READY=1 / WATCHDOG=1 a systemd (Type=notify + WatchdogSec). No hace nada fuera de systemd."""

    def __init__(self, interval_s: float = 15.0):
        import os, socket
        self.addr = os.environ.get("NOTIFY_SOCKET")
        self.interval = interval_s
        self._last = 0.0
        self._sock = None
        if self.addr:
            if self.addr.startswith("@"):
                self.addr = "\0" + self.addr[1:]  # socket abstracto de systemd
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            except Exception:
                self._sock = None

    def _send(self, msg: str) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(msg.encode(), self.addr)
        except Exception:
            pass

    def ready(self) -> None:
        self._send("READY=1")

    def ping(self) -> None:
        now = time.time()
        if now - self._last >= self.interval:
            self._last = now
            self._send("WATCHDOG=1")


def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, lc["level"].upper(), logging.INFO))
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt); root.addHandler(sh)
    log_path = (BASE_DIR / lc["file"]).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(log_path, maxBytes=lc["max_mb"] * 1024 * 1024, backupCount=lc["backups"])
    fh.setFormatter(fmt); root.addHandler(fh)
    logging.getLogger("ultralytics").setLevel(logging.WARNING)


def load_config(path: Path, overrides: argparse.Namespace) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if overrides.source is not None:
        cfg["camera"]["source"] = int(overrides.source) if overrides.source.isdigit() else overrides.source
    if overrides.no_cloud:
        cfg["cloud"]["enabled"] = False
        cfg.setdefault("cloud_v2", {})["enabled"] = False
    if overrides.port:
        cfg["local_server"]["port"] = overrides.port
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="Nodo de borde — detección de EPP")
    ap.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    ap.add_argument("--source", help="índice de cámara, archivo de video o URL RTSP (sobrescribe config)")
    ap.add_argument("--no-cloud", action="store_true", help="desactiva la sincronización con la nube")
    ap.add_argument("--port", type=int, help="puerto del servidor local")
    ap.add_argument("--max-seconds", type=float, default=0, help="terminar tras N segundos (pruebas)")
    args = ap.parse_args()

    cfg = load_config(Path(args.config), args)
    setup_logging(cfg)
    log = logging.getLogger("epp.main")
    log.info("Iniciando nodo %s (%s)", cfg["device"]["id"], cfg["device"]["zona"])

    storage = Storage((BASE_DIR / cfg["storage"]["db_path"]).resolve())
    frames = FrameBuffer(cfg["local_server"]["stream_jpeg_quality"], cfg["local_server"]["stream_fps"])

    cam = Camera(cfg["camera"]["source"], cfg["camera"]["width"], cfg["camera"]["height"], cfg["camera"]["fps"],
                 cfg["camera"]["flip"], cfg["camera"]["loop_video"], base_dir=BASE_DIR,
                 controls=cfg["camera"].get("controls")).start()

    alerter = Alerter(cfg, BASE_DIR)
    fsm = EPPStateMachine(cfg, storage, BASE_DIR, on_frame=frames.push, on_episode_closed=alerter.on_episode)
    notifier = SystemdNotifier()
    notifier.ready()

    def status() -> dict:
        st = fsm.status()
        paused = bool(v2 and not v2["control"].should_run())
        st.update({"reported_state": "stopped" if paused else "running", "fps": st.get("fps_epp"),
                   "epp_ms_p50": st.get("ms_epp_avg"), "cpu_temp_c": cpu_temperature(),
                   "cloud_v2_pending": v2["outbox"].stats()["pending"] if v2 else None,
                   "cloud_v2_synced": v2["uploader"].synced if v2 else 0})
        st.update({"device_id": cfg["device"]["id"], "zona": cfg["device"]["zona"], "camera_ok": cam.healthy,
                   "camera_reconnects": cam.reconnects, "uptime_s": int(time.time() - t_start),
                   "cloud_online": syncer.online if syncer else None, "cloud_pending": storage.pending_count(),
                   "cloud_synced": syncer.synced if syncer else 0, "live_mode": live.mode if live else None,
                   "live_frames_sent": live.sent if live else 0})
        return st

    t_start = time.time()
    syncer = live = server = v2 = None
    if cfg["local_server"]["enabled"]:
        server = LocalServer(cfg["local_server"]["host"], cfg["local_server"]["port"], frames,
                             status, lambda: storage.recent_episodes(50), lambda: cam.healthy).start()
    if cfg.get("cloud_v2", {}).get("enabled"):
        from .cloud_v2 import start_cloud_v2
        src = cfg["camera"]["source"]
        simulated = isinstance(src, str) and not src.startswith(("rtsp://", "http://", "https://"))
        try:
            v2 = start_cloud_v2(cfg, BASE_DIR, (BASE_DIR / cfg["storage"]["db_path"]).resolve(), frames, status,
                               simulated, cam=cam)
        except Exception as e:  # sin credenciales o sin librerías: el nodo sigue y guarda todo en local
            log.error("No se pudo iniciar la nube 2.0.0 (%s); los episodios quedan en la base local", e)
            v2 = None
    elif cfg["cloud"]["enabled"]:
        syncer = EpisodeSyncer(cfg, storage, BASE_DIR, status); syncer.start()
        if cfg["cloud"]["live_stream"]:
            live = LiveStreamer(cfg, frames, BASE_DIR, lambda: fsm.state.value); live.start()

    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    last_id = 0
    last_purge = time.time()
    was_running = True
    try:
        while not stop["flag"]:
            frame, last_id, ts = cam.wait_new(last_id, timeout=1.0)
            if frame is None:
                continue
            running = v2["control"].should_run() if v2 else True
            if running != was_running:          # pausa o reanudación ordenada desde el panel (rol operator)
                log.info("Detección %s desde el panel", "reanudada" if running else "pausada")
                if not running:
                    fsm.flush(reason="stopped")
                v2["health"].notify()
                was_running = running
            if running:
                fsm.step(frame)
            else:
                frames.push(frame, "PAUSED")
            notifier.ping()
            if args.max_seconds and time.time() - t_start > args.max_seconds:
                break
            if time.time() - last_purge > 3600:
                last_purge = time.time()
                n = storage.purge_old_snapshots(fsm.snapshots_dir, cfg["storage"]["keep_days"])
                if n:
                    log.info("Snapshots antiguos eliminados: %d", n)
    finally:
        log.info("Deteniendo…")
        fsm.flush()
        cam.stop()
        if live: live.stop()
        if v2:
            if v2["streamer"]: v2["streamer"].stop()
            if v2["capture"]: v2["capture"].stop()
            v2["uploader"].stop()
            try:                                  # último intento de vaciar la outbox (máx. ~10 s)
                t_end = time.time() + 10
                while time.time() < t_end and v2["uploader"].tick(drain=True):
                    pass
            except Exception:
                pass
            v2["health"].final()
            v2["control"].close()
        if syncer:
            syncer.stop()
            # último intento de vaciar la cola antes de salir
            try:
                syncer._heartbeat(); syncer._drain()
            except Exception:
                pass
        if server: server.stop()
        storage.close()
        log.info("Estadísticas finales: %s", fsm.status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
