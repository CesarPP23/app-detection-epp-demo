"""WebSocket "En vivo" del navegador: reenvía los frames del relay y mantiene el lease de streaming.

Mientras este WebSocket esté abierto, `device_control.stream_requested` queda en True y la Pi
transmite. Al cerrarse el último espectador el lease se apaga. Los frames se reenvían tal cual
(protocolo 1.2); los mensajes de texto son JSON {"type": "status", "state": ...}.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import relay
from .lease import StreamingLease
from .settings import settings

log = logging.getLogger("live")
router = APIRouter()


# --- Acoplamiento con auth.py / audit.py / repo.py (módulos de 3-A), resuelto al usarse ---
async def _authenticate(ws: WebSocket):
    """Cookie de sesión + Origin == PUBLIC_BASE_URL. Cierra el socket (1008) y devuelve None si falla."""
    from .auth import authenticate_ws

    return await authenticate_ws(ws)


def _audit(email: str, action: str, target: str) -> None:
    from . import audit

    try:
        audit.write(email, action, target)
    except Exception:  # noqa: BLE001 - la auditoría no debe tumbar el video
        log.exception("No se pudo registrar %s", action)


async def _set_stream_requested(device_id: str, value: bool, email: str) -> None:
    from . import repo

    await repo.set_stream_requested(device_id, value, email)


async def _renew_lease(device_id: str) -> None:
    from . import repo

    await repo.renew_lease(device_id)


lease = StreamingLease(_set_stream_requested, _renew_lease, settings.lease_seconds / 3)

# Si no llega ningún frame en este tiempo se avisa "waiting" (la Pi aún no transmite o se cortó).
_WAIT_SECONDS = 5


async def _status(ws: WebSocket, state: str) -> None:
    await ws.send_json({"type": "status", "state": state})


@router.websocket("/api/devices/{device_id}/live")
async def live(ws: WebSocket, device_id: str):
    user = await _authenticate(ws)  # si falla ya cerró con 1008
    if user is None:
        return
    if device_id not in settings.allowed_devices:
        await ws.close(code=1008)
        return
    if lease.viewers >= settings.max_live_viewers:
        await ws.close(code=1013)  # "intenta más tarde"
        return

    await ws.accept()
    ticket = await lease.acquire(device_id, user.email)
    _audit(user.email, "live_start", device_id)
    pump = asyncio.create_task(_pump(ws, device_id))
    reader = asyncio.create_task(_wait_disconnect(ws))
    try:
        # Lo primero que termine gana: el cliente se fue (reader) o el envío falló (pump).
        await asyncio.wait({pump, reader}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (pump, reader):
            task.cancel()
        # shield: si el servidor cancela el handler al cerrar el socket, el lease igual se libera.
        await asyncio.shield(_cleanup(ticket, user.email, device_id, pump, reader))


async def _cleanup(ticket, email: str, device_id: str, *tasks: asyncio.Task) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)
    await lease.release(ticket)
    _audit(email, "live_end", device_id)


async def _wait_disconnect(ws: WebSocket) -> None:
    """Consume lo que envíe el cliente y termina cuando se desconecta (libera el lease sin esperar
    al siguiente envío)."""
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError):
        return


async def _pump(ws: WebSocket, device_id: str) -> None:
    ch = relay.channel(device_id)
    seq, last_sent = 0, 0.0
    min_interval = 1.0 / max(1, settings.live_max_fps)
    try:
        await _status(ws, "live" if ch.live else "waiting")
        while True:
            try:
                seq, frame = await ch.next_frame(seq, timeout=_WAIT_SECONDS)
            except asyncio.TimeoutError:
                await _status(ws, "waiting")
                continue
            if frame is None:
                await _status(ws, "ended")
                continue
            now = time.monotonic()
            if now - last_sent >= min_interval:
                # next_frame siempre entrega el más reciente: un cliente lento se salta frames.
                await ws.send_bytes(frame)
                last_sent = now
    except (WebSocketDisconnect, RuntimeError):
        pass  # envío sobre un socket que el cliente ya cerró
