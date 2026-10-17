"""Lease de streaming: mientras haya al menos un espectador en "En vivo", el backend marca
`device_control.stream_requested=True` y renueva `lease_renewed_at`; con 0 espectadores lo apaga.

Las escrituras se inyectan como callables async para no depender de repo.py:
  set_stream_requested(device_id, value, email)  -> escribe stream_requested (y, al encender,
                                                    también lease_renewed_at=SERVER_TIMESTAMP)
  renew_lease(device_id)                         -> escribe lease_renewed_at=SERVER_TIMESTAMP

La renovación corre en una tarea asyncio: mientras haya un WebSocket abierto la instancia
tiene CPU (--cpu-throttling factura por request), así que la tarea avanza.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("lease")

SetStreamRequested = Callable[[str, bool, str], Awaitable[None]]
RenewLease = Callable[[str], Awaitable[None]]


@dataclass(eq=False)
class Ticket:
    """Un espectador. `release` es idempotente: solo la primera llamada cuenta."""

    device_id: str
    email: str
    released: bool = field(default=False, repr=False)


class StreamingLease:
    def __init__(
        self,
        set_stream_requested: SetStreamRequested,
        renew_lease: RenewLease,
        interval_seconds: float,
    ) -> None:
        self._set = set_stream_requested
        self._renew = renew_lease
        self.interval = max(0.01, interval_seconds)
        self._counts: dict[str, int] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, device_id: str) -> asyncio.Lock:
        return self._locks.setdefault(device_id, asyncio.Lock())

    @property
    def viewers(self) -> int:
        """Espectadores totales (todos los dispositivos): es el tope de MAX_LIVE_VIEWERS."""
        return sum(self._counts.values())

    def viewers_of(self, device_id: str) -> int:
        return self._counts.get(device_id, 0)

    async def acquire(self, device_id: str, email: str) -> Ticket:
        ticket = Ticket(device_id, email)
        async with self._lock(device_id):
            self._counts[device_id] = self._counts.get(device_id, 0) + 1
            if self._counts[device_id] == 1:
                await self._safe(self._set(device_id, True, email), "encender stream_requested")
                self._tasks[device_id] = asyncio.create_task(
                    self._renew_loop(device_id), name=f"lease-renew-{device_id}"
                )
        return ticket

    async def release(self, ticket: Ticket) -> None:
        if ticket.released:
            return
        ticket.released = True
        device_id = ticket.device_id
        async with self._lock(device_id):
            left = max(0, self._counts.get(device_id, 0) - 1)
            if left:
                self._counts[device_id] = left
                return
            self._counts.pop(device_id, None)
            task = self._tasks.pop(device_id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self._safe(self._set(device_id, False, ticket.email), "apagar stream_requested")

    async def _renew_loop(self, device_id: str) -> None:
        while True:
            await asyncio.sleep(self.interval)
            await self._safe(self._renew(device_id), "renovar lease")

    @staticmethod
    async def _safe(awaitable: Awaitable[None], what: str) -> None:
        """Un fallo de Firestore no debe tumbar al espectador ni dejar el contador inconsistente."""
        try:
            await awaitable
        except Exception:  # noqa: BLE001
            log.exception("Lease: no se pudo %s", what)
