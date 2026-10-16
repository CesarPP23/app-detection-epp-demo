"""Servidor HTTP local (LAN) para validar el nodo en planta sin depender de internet.

  GET /              página mínima con el stream y el estado
  GET /stream.mjpg   video anotado (MJPEG)
  GET /snapshot.jpg  último frame anotado
  GET /status        JSON: estado de la FSM, fps, cola de sync, temperatura CPU
  GET /episodes      JSON: últimos 50 episodios de la BD local
  GET /health        200 si la cámara entrega frames

Usa solo la librería estándar (ThreadingHTTPServer) para no añadir dependencias al Pi.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

import cv2
import numpy as np

log = logging.getLogger("epp.local_server")

INDEX_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>EPP Edge</title>
<style>body{font-family:system-ui;margin:16px;background:#111;color:#eee}img{max-width:100%;border:1px solid #444}
pre{background:#222;padding:8px;font-size:12px;overflow:auto}</style></head><body>
<h2>Nodo EPP — vista local</h2><img src="/stream.mjpg" alt="stream"><h3>Estado</h3><pre id="s">…</pre>
<script>setInterval(async()=>{const r=await fetch('/status');document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)},1000)</script>
</body></html>"""


class FrameBuffer:
    """Último frame anotado en JPEG, compartido entre la FSM y los clientes HTTP/WS."""

    def __init__(self, jpeg_quality: int = 70, max_fps: float = 5.0):
        self._lock = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._seq = 0
        self._ts = 0.0
        self.q = jpeg_quality
        self.min_period = 1.0 / max_fps
        self.state = "IDLE"

    def push(self, frame: np.ndarray, state: str = "") -> None:
        now = time.time()
        if now - self._ts < self.min_period:
            return
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.q])
        if not ok:
            return
        with self._lock:
            self._jpeg = buf.tobytes()
            self._seq += 1
            self._ts = now
            self.state = state or self.state
            self._lock.notify_all()

    def latest(self) -> tuple[Optional[bytes], int]:
        with self._lock:
            return self._jpeg, self._seq

    def wait(self, last_seq: int, timeout: float = 1.0) -> tuple[Optional[bytes], int]:
        with self._lock:
            if self._seq == last_seq:
                self._lock.wait(timeout)
            return self._jpeg, self._seq


def cpu_temperature() -> Optional[float]:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


class LocalServer:
    def __init__(self, host: str, port: int, frames: FrameBuffer,
                 status_fn: Callable[[], dict], episodes_fn: Callable[[], list], health_fn: Callable[[], bool]):
        self.frames = frames
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silenciar log por request
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    body = INDEX_HTML.encode()
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                elif path == "/status":
                    st = status_fn(); st["cpu_temp_c"] = cpu_temperature(); self._json(st)
                elif path == "/episodes":
                    self._json(episodes_fn())
                elif path == "/health":
                    self._json({"ok": health_fn()}, 200 if health_fn() else 503)
                elif path == "/snapshot.jpg":
                    jpg, _ = outer.frames.latest()
                    if jpg is None:
                        self.send_error(404); return
                    self.send_response(200); self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg))); self.end_headers(); self.wfile.write(jpg)
                elif path == "/stream.mjpg":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache"); self.end_headers()
                    seq = -1
                    try:
                        while True:
                            jpg, seq_new = outer.frames.wait(seq, timeout=2.0)
                            if jpg is None:
                                continue
                            if seq_new == seq:
                                # sin frame nuevo: reenviar el último para mantener viva la conexión
                                pass
                            seq = seq_new
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                             str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                else:
                    self.send_error(404)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="local-http", daemon=True)
        self.host, self.port = host, port

    def start(self) -> "LocalServer":
        self._thread.start()
        log.info("Servidor local en http://%s:%d/", self.host, self.port)
        return self

    def stop(self) -> None:
        self._server.shutdown()
