"""Relay en memoria: la Pi publica frames; los espectadores (WebSocket del navegador,
en este mismo proceso) esperan el siguiente frame NUEVO. Requiere max-instances=1 y 1 worker."""

import asyncio
import hmac
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .settings import settings

log = logging.getLogger("relay")
router = APIRouter()
_HEADER = 8
_JPEG_SOI = b"\xff\xd8"


def token_ok(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    if provided.startswith("Bearer "):
        provided = provided[7:]
    return hmac.compare_digest(provided.encode(), expected.encode())


def valid_frame(data: bytes) -> bool:
    return _HEADER + 2 < len(data) <= settings.max_frame_bytes and data[_HEADER : _HEADER + 2] == _JPEG_SOI


class DeviceChannel:
    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.seq = 0
        self.publisher_id = 0  # la conexión más nueva de la Pi gana
        self._cond = asyncio.Condition()

    @property
    def live(self) -> bool:
        return self.frame is not None

    async def publish(self, frame: bytes) -> None:
        async with self._cond:
            self.frame, self.seq = frame, self.seq + 1
            self._cond.notify_all()

    async def close(self, publisher_id: int) -> None:
        async with self._cond:
            if publisher_id != self.publisher_id:
                return
            self.frame, self.seq = None, self.seq + 1
            self._cond.notify_all()

    async def next_frame(self, last_seq: int, timeout: float) -> tuple[int, bytes | None]:
        """(seq, frame). frame=None => la Pi se desconectó. asyncio.TimeoutError => sin frames."""
        async with self._cond:
            await asyncio.wait_for(self._cond.wait_for(lambda: self.seq != last_seq), timeout)
            return self.seq, self.frame


channels: dict[str, DeviceChannel] = {}


def channel(device_id: str) -> DeviceChannel:
    return channels.setdefault(device_id, DeviceChannel())


@router.websocket("/ws/video/{device_id}")
async def ws_video(websocket: WebSocket, device_id: str):
    if device_id not in settings.allowed_devices or not token_ok(
        websocket.headers.get("authorization"), settings.device_token
    ):
        log.warning("Uplink rechazado device=%s", device_id)
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ch = channel(device_id)
    ch.publisher_id += 1
    my_id = ch.publisher_id
    log.info("Uplink conectado device=%s", device_id)
    try:
        while True:
            data = await asyncio.wait_for(websocket.receive_bytes(), timeout=settings.publisher_idle_seconds)
            if valid_frame(data):
                await ch.publish(data)
    except (TimeoutError, WebSocketDisconnect):
        pass
    finally:
        await ch.close(my_id)
        log.info("Uplink cerrado device=%s", device_id)
