"""Firestore falso en memoria con el subconjunto de la API que usa repo.py. Cuenta lecturas."""

import threading
from datetime import UTC, datetime

from google.cloud import firestore


def _resolve(value):
    if value is firestore.SERVER_TIMESTAMP:
        return datetime.now(UTC)
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    return value


class FakeSnap:
    def __init__(self, doc_id: str, data: dict | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, db: "FakeDB", col: str, doc_id: str) -> None:
        self._db, self._col, self.id = db, col, doc_id

    def get(self):
        self._db.reads += 1
        return FakeSnap(self.id, self._db.data[self._col].get(self.id))

    def set(self, data, merge=False):
        data = _resolve(data)
        store = self._db.data[self._col]
        if merge and self.id in store:
            store[self.id] = {**store[self.id], **data}
        else:
            store[self.id] = dict(data)
        self._db.writes += 1

    def update(self, data):
        store = self._db.data[self._col]
        if self.id not in store:
            raise KeyError(self.id)
        store[self.id] = {**store[self.id], **_resolve(data)}
        self._db.writes += 1

    def delete(self):
        self._db.data[self._col].pop(self.id, None)
        self._db.writes += 1


class FakeQuery:
    def __init__(self, db: "FakeDB", col: str) -> None:
        self._db, self._col = db, col
        self._filters: list = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def _copy(self):
        q = FakeQuery(self._db, self._col)
        q._filters, q._order, q._limit = list(self._filters), self._order, self._limit
        return q

    def where(self, *args, filter=None):  # noqa: A002
        q = self._copy()
        q._filters.append((filter.field_path, filter.op_string, filter.value))
        return q

    def order_by(self, field, direction="ASCENDING"):
        q = self._copy()
        q._order = (field, direction == "DESCENDING")
        return q

    def limit(self, n):
        q = self._copy()
        q._limit = n
        return q

    def add(self, data):
        doc_id = f"auto-{len(self._db.data[self._col]) + 1:06d}"
        FakeDocRef(self._db, self._col, doc_id).set(data)
        return None, FakeDocRef(self._db, self._col, doc_id)

    def document(self, doc_id):
        return FakeDocRef(self._db, self._col, doc_id)

    def stream(self):
        rows = [(i, dict(d)) for i, d in self._db.data[self._col].items()]
        ops = {
            "==": lambda a, b: a == b,
            ">": lambda a, b: a is not None and a > b,
            ">=": lambda a, b: a is not None and a >= b,
            "<": lambda a, b: a is not None and a < b,
            "<=": lambda a, b: a is not None and a <= b,
        }
        for field, op, value in self._filters:
            rows = [(i, d) for i, d in rows if ops[op](d.get(field), value)]
        if self._order:
            field, desc = self._order
            rows.sort(key=lambda r: (r[1].get(field), r[0]), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        self._db.reads += max(1, len(rows))
        return iter([FakeSnap(i, d) for i, d in rows])


class FakeDB:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict]] = {}
        self.reads = 0
        self.writes = 0
        self._lock = threading.Lock()

    def collection(self, name: str) -> FakeQuery:
        self.data.setdefault(name, {})
        return FakeQuery(self, name)
