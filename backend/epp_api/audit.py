"""Registro de auditoría (`audit_log` en la base de control). Las escrituras no bloquean la request."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

log = logging.getLogger("audit")

RETENTION_DAYS = 365  # TTL de Firestore sobre `expire_at` (Fase 4.4)


class Audit:
    def __init__(self, writer: Callable[[dict], None]) -> None:
        self._writer = writer
        self._tasks: set[asyncio.Task] = set()

    def write(self, email: str, action: str, target: str | None = None, detail: str | None = None) -> None:
        now = datetime.now(UTC)
        doc = {
            "ts": now,
            "email": email,
            "action": action,
            "target": target,
            "detail": detail,
            "expire_at": now + timedelta(days=RETENTION_DAYS),
        }
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write_now(doc)
            return
        task = loop.create_task(self._write_async(doc))
        self._tasks.add(task)  # referencia fuerte: evita que el GC cancele la tarea
        task.add_done_callback(self._tasks.discard)

    def _write_now(self, doc: dict) -> None:
        try:
            self._writer(doc)
        except Exception:
            log.exception("No se pudo escribir en audit_log (action=%s)", doc.get("action"))

    async def _write_async(self, doc: dict) -> None:
        await asyncio.to_thread(self._write_now, doc)

    async def flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


_audit: Audit | None = None


def get_audit() -> Audit:
    global _audit
    if _audit is None:
        from .repo import get_repo

        _audit = Audit(lambda doc: get_repo().write_audit_sync(doc))
    return _audit


def write(email: str, action: str, target: str | None = None, detail: str | None = None) -> None:
    """Atajo de módulo (lo usa live.py): escribe con la instancia compartida."""
    get_audit().write(email, action, target, detail)
