"""Acceso a Firestore con caché incremental compartida entre usuarios (3.2).

No se usan listeners: con --cpu-throttling la instancia no tiene CPU entre requests y quedarían
congelados. En su lugar, cada clave se refresca como mucho una vez por TTL, y solo se piden a
Firestore los documentos más nuevos que los ya cacheados.
"""

import asyncio
import base64
import binascii
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import ApiError
from .settings import settings

log = logging.getLogger("repo")

EVENTS = "detecciones_confirmadas"
DAILY_STATS = "daily_stats"
DEVICE_CONTROL = "device_control"
DEVICE_HEALTH = "device_health"
USERS = "dashboard_users"
AUDIT = "audit_log"

WINDOW = 500  # eventos recientes por dispositivo que se mantienen en memoria
STATS_DAYS = 60
USER_TTL = 60.0
DEVICE_TTL = 5.0
STATS_TTL = 60.0
_MAX_SCAN_BATCHES = 6


def utcnow() -> datetime:
    return datetime.now(UTC)


def _event_sort_key(e: dict) -> tuple:
    return (e["end_ts"], e["event_id"])


def encode_cursor(end_ts: datetime, item_id: str) -> str:
    return base64.urlsafe_b64encode(f"{end_ts.isoformat()}|{item_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, item_id = raw.split("|", 1)
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt, item_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as e:
        raise ApiError(400, "cursor_invalido", "El cursor de paginación no es válido.") from e


@dataclass
class EventFilters:
    clase: str | None = None
    min_conf: float | None = None
    from_: datetime | None = None
    to: datetime | None = None
    include_simulated: bool = False

    def match(self, e: dict) -> bool:
        if self.clase and e.get("nombre_clase") != self.clase:
            return False
        if self.min_conf is not None and (e.get("confianza_max") or 0.0) < self.min_conf:
            return False
        if not self.include_simulated and e.get("is_simulated"):
            return False
        if self.from_ and e["end_ts"] < self.from_:
            return False
        return not (self.to and e["end_ts"] > self.to)


@dataclass
class _Window:
    events: list[dict] = field(default_factory=list)  # más nuevo primero
    max_end: datetime | None = None
    complete: bool = False  # True: no hay eventos más viejos que los cacheados
    at: float = 0.0


@dataclass
class _Timed:
    value: object
    at: float


class Repo:
    def __init__(
        self,
        control_db,
        events_db,
        *,
        ttl: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.control_db = control_db
        self.events_db = events_db
        self.ttl = float(settings.cache_ttl_seconds if ttl is None else ttl)
        self._clock = clock
        self._now = now
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._windows: dict[str, _Window] = {}
        self._users: dict[str, _Timed] = {}
        self._device_docs: dict[tuple[str, str], _Timed] = {}
        self._stats: dict[str, dict[str, dict]] = {}
        self._stats_at: dict[str, float] = {}

    # --- utilidades ---------------------------------------------------------------------------

    def _fresh(self, entry: _Timed | None, ttl: float) -> bool:
        return entry is not None and self._clock() - entry.at < ttl

    def _local_today(self) -> date:
        return self._now().astimezone(ZoneInfo(settings.site_timezone)).date()

    async def _run(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    # --- usuarios del dashboard ---------------------------------------------------------------

    async def get_user(self, email: str) -> dict | None:
        email = email.strip().lower()
        async with self._locks[f"user:{email}"]:
            cached = self._users.get(email)
            if self._fresh(cached, USER_TTL):
                return cached.value  # type: ignore[return-value]
            snap = await self._run(self.control_db.collection(USERS).document(email).get)
            value = {"email": email, **(snap.to_dict() or {})} if snap.exists else None
            self._users[email] = _Timed(value, self._clock())
            return value

    async def list_users(self) -> list[dict]:
        snaps = await self._run(lambda: list(self.control_db.collection(USERS).stream()))
        users = [{"email": s.id, **(s.to_dict() or {})} for s in snaps]
        return sorted(users, key=lambda u: u["email"])

    async def create_user(self, email: str, role: str, created_by: str) -> dict:
        email = email.strip().lower()
        data = {"role": role, "active": True, "created_by": created_by, "created_at": utcnow()}
        ref = self.control_db.collection(USERS).document(email)
        snap = await self._run(ref.get)
        if snap.exists:
            raise ApiError(409, "usuario_existente", "Ese usuario ya existe.")
        await self._run(ref.set, data)
        self._users.pop(email, None)
        return {"email": email, **data}

    async def update_user(self, email: str, changes: dict) -> None:
        email = email.strip().lower()
        await self._run(self.control_db.collection(USERS).document(email).update, changes)
        self._users.pop(email, None)

    async def delete_user(self, email: str) -> None:
        email = email.strip().lower()
        await self._run(self.control_db.collection(USERS).document(email).delete)
        self._users.pop(email, None)

    # --- control y salud del dispositivo ------------------------------------------------------

    async def _device_doc(self, collection: str, device_id: str) -> dict:
        key = (collection, device_id)
        async with self._locks[f"{collection}:{device_id}"]:
            cached = self._device_docs.get(key)
            if self._fresh(cached, DEVICE_TTL):
                return cached.value  # type: ignore[return-value]
            snap = await self._run(self.control_db.collection(collection).document(device_id).get)
            value = (snap.to_dict() or {}) if snap.exists else {}
            self._device_docs[key] = _Timed(value, self._clock())
            return value

    async def get_control(self, device_id: str) -> dict:
        return await self._device_doc(DEVICE_CONTROL, device_id)

    async def get_health(self, device_id: str) -> dict:
        return await self._device_doc(DEVICE_HEALTH, device_id)

    def _write_control(self, device_id: str, data: dict) -> None:
        self.control_db.collection(DEVICE_CONTROL).document(device_id).set(data, merge=True)

    async def set_desired_state(self, device_id: str, state: str, by: str) -> None:
        from google.cloud import firestore

        data = {"desired_state": state, "updated_by": by, "updated_at": firestore.SERVER_TIMESTAMP}
        await self._run(self._write_control, device_id, data)
        self._device_docs.pop((DEVICE_CONTROL, device_id), None)

    async def set_stream_requested(self, device_id: str, requested: bool, by: str | None = None) -> None:
        from google.cloud import firestore

        data: dict = {"stream_requested": requested, "updated_at": firestore.SERVER_TIMESTAMP}
        if by:
            data["updated_by"] = by
        if requested:
            data["lease_renewed_at"] = firestore.SERVER_TIMESTAMP
            data["lease_seconds"] = settings.lease_seconds
        await self._run(self._write_control, device_id, data)
        self._device_docs.pop((DEVICE_CONTROL, device_id), None)

    async def renew_lease(self, device_id: str) -> None:
        from google.cloud import firestore

        data = {
            "stream_requested": True,
            "lease_renewed_at": firestore.SERVER_TIMESTAMP,
            "lease_seconds": settings.lease_seconds,
        }
        await self._run(self._write_control, device_id, data)
        self._device_docs.pop((DEVICE_CONTROL, device_id), None)

    # --- eventos: ventana incremental ---------------------------------------------------------

    def _events_col(self):
        return self.events_db.collection(EVENTS)

    @staticmethod
    def _event(snap) -> dict:
        data = snap.to_dict() or {}
        data["event_id"] = snap.id
        return data

    def _initial_window(self, device_id: str) -> list[dict]:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = (
            self._events_col()
            .where(filter=FieldFilter("device_id", "==", device_id))
            .order_by("end_ts", direction=firestore.Query.DESCENDING)
            .limit(WINDOW)
        )
        return [self._event(s) for s in q.stream()]

    def _newer_than(self, device_id: str, after: datetime) -> list[dict]:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = (
            self._events_col()
            .where(filter=FieldFilter("device_id", "==", device_id))
            .where(filter=FieldFilter("end_ts", ">", after))
            .order_by("end_ts", direction=firestore.Query.ASCENDING)
        )
        return [self._event(s) for s in q.stream()]

    async def events_window(self, device_id: str) -> _Window:
        async with self._locks[f"window:{device_id}"]:
            win = self._windows.get(device_id)
            if win is not None and self._clock() - win.at < self.ttl:
                return win
            if win is None or win.max_end is None:
                docs = await self._run(self._initial_window, device_id)
                events = sorted(docs, key=_event_sort_key, reverse=True)
                win = _Window(events=events, complete=len(docs) < WINDOW)
            else:
                new = await self._run(self._newer_than, device_id, win.max_end)
                if new:
                    known = {e["event_id"] for e in win.events}
                    fresh = [e for e in new if e["event_id"] not in known]
                    merged = sorted(fresh + win.events, key=_event_sort_key, reverse=True)
                    if len(merged) > WINDOW:
                        merged, win.complete = merged[:WINDOW], False
                    win.events = merged
            win.max_end = win.events[0]["end_ts"] if win.events else win.max_end
            win.at = self._clock()
            self._windows[device_id] = win
            return win

    # --- eventos: lista, detalle, filtros -----------------------------------------------------

    def _scan_batch(self, device_id, flt: EventFilters, upper: datetime | None, size: int) -> list[dict]:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = self._events_col().where(filter=FieldFilter("device_id", "==", device_id))
        if flt.clase:
            q = q.where(filter=FieldFilter("nombre_clase", "==", flt.clase))
        if flt.from_:
            q = q.where(filter=FieldFilter("end_ts", ">=", flt.from_))
        if upper:
            q = q.where(filter=FieldFilter("end_ts", "<=", upper))
        q = q.order_by("end_ts", direction=firestore.Query.DESCENDING).limit(size)
        return [self._event(s) for s in q.stream()]

    async def list_events(
        self, device_id: str, flt: EventFilters, cursor: str | None, limit: int
    ) -> tuple[list[dict], str | None]:
        """Devuelve (eventos, next_cursor). Orden: end_ts desc, event_id desc."""
        if cursor is None:
            win = await self.events_window(device_id)
            items = [e for e in win.events if flt.match(e)]
            oldest = win.events[-1]["end_ts"] if win.events else None
            covered = win.complete or (flt.from_ is not None and oldest is not None and oldest <= flt.from_)
            if len(items) > limit or covered:
                return self._page(items, limit)
        cur_dt, cur_id = decode_cursor(cursor) if cursor else (None, None)
        upper = min([d for d in (flt.to, cur_dt) if d], default=None)
        out: list[dict] = []
        batch = max(limit * 2, 24)
        for _ in range(_MAX_SCAN_BATCHES):
            docs = await self._run(self._scan_batch, device_id, flt, upper, batch)
            for e in docs:
                if cur_dt is not None and (e["end_ts"], e["event_id"]) >= (cur_dt, cur_id):
                    continue
                if flt.match(e):
                    out.append(e)
            if len(docs) < batch or len(out) > limit:
                break
            last = docs[-1]
            upper, cur_dt, cur_id = last["end_ts"], last["end_ts"], last["event_id"]
        return self._page(out, limit)

    @staticmethod
    def _page(items: list[dict], limit: int) -> tuple[list[dict], str | None]:
        if len(items) > limit:
            page = items[:limit]
            return page, encode_cursor(page[-1]["end_ts"], page[-1]["event_id"])
        return items, None

    async def get_event(self, event_id: str) -> dict | None:
        for win in self._windows.values():
            for e in win.events:
                if e["event_id"] == event_id:
                    return e
        snap = await self._run(self._events_col().document(event_id).get)
        return self._event(snap) if snap.exists else None

    # --- daily_stats --------------------------------------------------------------------------

    def _stats_query(self, device_id: str, since: date) -> list[dict]:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = (
            self.events_db.collection(DAILY_STATS)
            .where(filter=FieldFilter("device_id", "==", device_id))
            .where(filter=FieldFilter("date", ">=", since.isoformat()))
            .order_by("date", direction=firestore.Query.ASCENDING)
        )
        return [s.to_dict() or {} for s in q.stream()]

    async def daily_stats(self, device_id: str) -> dict[str, dict]:
        """{'YYYY-MM-DD': {events, seconds, by_class}}. Los días pasados se piden una sola vez;
        luego solo se refrescan ayer y hoy."""
        async with self._locks[f"stats:{device_id}"]:
            today = self._local_today()
            have = self._stats.get(device_id)
            if have is not None and self._clock() - self._stats_at[device_id] < STATS_TTL:
                return have
            since = today - timedelta(days=STATS_DAYS - 1) if have is None else today - timedelta(days=1)
            docs = await self._run(self._stats_query, device_id, since)
            have = have or {}
            for d in docs:
                if d.get("date"):
                    have[d["date"]] = d
            cutoff = (today - timedelta(days=STATS_DAYS - 1)).isoformat()
            have = {k: v for k, v in have.items() if k >= cutoff}
            self._stats[device_id] = have
            self._stats_at[device_id] = self._clock()
            return have

    # --- auditoría ----------------------------------------------------------------------------

    def write_audit_sync(self, doc: dict) -> None:
        self.control_db.collection(AUDIT).add(doc)

    def _audit_batch(self, upper: datetime | None, size: int) -> list[dict]:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = self.control_db.collection(AUDIT)
        if upper:
            q = q.where(filter=FieldFilter("ts", "<=", upper))
        q = q.order_by("ts", direction=firestore.Query.DESCENDING).limit(size)
        return [{"id": s.id, **(s.to_dict() or {})} for s in q.stream()]

    async def list_audit(
        self, cursor: str | None, action: str | None, email: str | None, limit: int
    ) -> tuple[list[dict], str | None]:
        cur_dt, cur_id = decode_cursor(cursor) if cursor else (None, None)
        upper = cur_dt
        out: list[dict] = []
        batch = max(limit * 2, 50)
        email = email.lower() if email else None
        for _ in range(_MAX_SCAN_BATCHES):
            docs = await self._run(self._audit_batch, upper, batch)
            for d in docs:
                if cur_dt is not None and (d["ts"], d["id"]) >= (cur_dt, cur_id):
                    continue
                if action and d.get("action") != action:
                    continue
                if email and (d.get("email") or "").lower() != email:
                    continue
                out.append(d)
            if len(docs) < batch or len(out) > limit:
                break
            upper, cur_dt, cur_id = docs[-1]["ts"], docs[-1]["ts"], docs[-1]["id"]
        if len(out) > limit:
            page = out[:limit]
            return page, encode_cursor(page[-1]["ts"], page[-1]["id"])
        return out, None


_repo: Repo | None = None


def get_repo() -> Repo:
    """Dependencia de FastAPI. Los clientes de Firestore se crean al primer uso (ADC de Cloud Run)."""
    global _repo
    if _repo is None:
        from google.cloud import firestore

        _repo = Repo(
            firestore.Client(project=settings.gcp_project_id, database=settings.control_database_id),
            firestore.Client(project=settings.gcp_project_id, database=settings.detections_database_id),
        )
    return _repo
