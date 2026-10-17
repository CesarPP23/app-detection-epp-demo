import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class LatestSlot(Generic[T]):
    """Buffer de un elemento: el productor sobrescribe y el consumidor siempre
    obtiene lo más reciente. Ningún consumidor lento acumula atraso."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._item: T | None = None
        self._seq = 0

    def put(self, item: T) -> None:
        with self._cond:
            self._item, self._seq = item, self._seq + 1
            self._cond.notify_all()

    def latest(self) -> T | None:
        with self._cond:
            return self._item

    def wait_newer(self, seq: int, timeout: float) -> tuple[int, T | None]:
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq != seq, timeout):
                return seq, None
            return self._seq, self._item
