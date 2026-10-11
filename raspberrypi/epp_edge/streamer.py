"""Subida de video solo bajo demanda (2.11).

Mientras no haya lease de un espectador (`control.should_stream()`), no existe ninguna conexión a
Cloud Run. El video sale a STREAM_FPS independiente de INFERENCE_FPS: se envía el frame más
fresco con las cajas de la última inferencia (corrige L1).
"""
import logging
import struct
import threading
import time

import cv2

from .annotate import annotate, resize_to_width


def pack_frame(captured_at_ms: int, jpeg: bytes) -> bytes:
    """Protocolo 1.2: uint64 big-endian (ms Unix de captura) + JPEG."""
    return struct.pack(">Q", captured_at_ms) + jpeg


class StreamWorker(threading.Thread):
    def __init__(
        self, settings, control, health, frames, results, stop: threading.Event, log: logging.Logger,
        connect=None,
    ) -> None:
        super().__init__(name="streamer", daemon=True)
        self.s, self.control, self.health = settings, control, health
        self.frames, self.results, self.stop_event, self.log = frames, results, stop, log
        self._connect = connect  # inyectable en tests; por defecto websockets.sync.client.connect

    def run(self) -> None:
        connect = self._connect
        if connect is None:
            from websockets.sync.client import connect  # import tardío
        backoff = 1.0
        while not self.stop_event.is_set():
            if not self.control.should_stream():
                self.stop_event.wait(0.25)
                continue
            try:
                with connect(
                    self.s.uplink_url,
                    additional_headers={"Authorization": f"Bearer {self.s.device_token}"},
                    compression=None,
                    open_timeout=10,
                    close_timeout=2,
                    max_size=2**20,
                ) as ws:
                    self.health.set_streaming(True)
                    self.log.info("Streaming conectado")
                    backoff, seq, period = 1.0, 0, 1.0 / self.s.stream_fps
                    while self.control.should_stream() and not self.stop_event.is_set():
                        t0 = time.monotonic()
                        seq, frame = self.frames.wait_newer(seq, timeout=1.0)
                        if frame is None:
                            continue
                        img = resize_to_width(annotate(frame, self.results.latest()), self.s.stream_width)
                        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.s.stream_jpeg_quality])
                        if ok:
                            ws.send(pack_frame(int(frame.ts_wall.timestamp() * 1000), buf.tobytes()))
                        self.stop_event.wait(max(0.0, period - (time.monotonic() - t0)))
                self.log.info("Streaming cerrado (sin espectadores)")
            except Exception as e:  # noqa: BLE001
                self.log.warning("Streaming caído: %s (reintento en %.0fs)", e, backoff)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self.health.set_streaming(False)
