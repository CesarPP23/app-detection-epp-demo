import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from .. import auth, summary
from ..audit import Audit, get_audit
from ..errors import ApiError
from ..lake import capture_status
from ..repo import EventFilters, Repo, get_repo
from ..schemas import (
    DesiredStateBody,
    DeviceHealth,
    DeviceStatus,
    EventPage,
    EventSummary,
    Summary,
    SummaryRange,
)
from ..settings import settings

router = APIRouter(tags=["devices"])

OFFLINE_AFTER_SECONDS = 11 * 60  # 2 latidos de 5 min + margen


def _device_or_404(device_id: str) -> str:
    if device_id not in settings.allowed_devices:
        raise ApiError(404, "dispositivo_no_encontrado", "No existe ese dispositivo.")
    return device_id


def event_summary(e: dict, now: datetime | None = None) -> EventSummary:
    """Fila de lista. La miniatura se pide por /api/events/{id}/capture, que firma la URL al vuelo."""
    cap = e.get("capture") or {}
    status = capture_status(cap.get("status"), e["end_ts"], now)
    return EventSummary(
        event_id=e["event_id"],
        clase_id=e.get("clase_id"),
        nombre_clase=e.get("nombre_clase", ""),
        confianza_promedio=e.get("confianza_promedio") or 0.0,
        confianza_max=e.get("confianza_max") or 0.0,
        frame_count=e.get("frame_count"),
        start_ts=e.get("start_ts") or e["end_ts"],
        end_ts=e["end_ts"],
        duration_seconds=e.get("duration_seconds") or 0.0,
        thumb_url=f"/api/events/{e['event_id']}/capture?kind=thumb" if status == "ok" else None,
        capture_status=status,  # type: ignore[arg-type]
        is_simulated=bool(e.get("is_simulated")),
    )


def device_state(control: dict, health: dict, now: datetime) -> str:
    last_seen = health.get("last_seen")
    if last_seen is None or (now - last_seen).total_seconds() > OFFLINE_AFTER_SECONDS:
        return "offline"
    if health.get("camera_ok") is False:
        return "camera_error"
    desired = control.get("desired_state", "running")
    if desired == "stopped" or health.get("reported_state") == "stopped":
        return "paused"
    if health.get("streaming"):
        return "live"
    return "detecting"


def build_device_status(device_id: str, control: dict, health: dict, now: datetime) -> DeviceStatus:
    return DeviceStatus(
        id=device_id,
        name=device_id,
        state=device_state(control, health, now),  # type: ignore[arg-type]
        desired_state=control.get("desired_state", "running"),
        last_seen=health.get("last_seen"),
        streaming=bool(health.get("streaming")),
        camera_ok=health.get("camera_ok"),
        health=DeviceHealth(**{k: health.get(k) for k in DeviceHealth.model_fields}),
    )


async def _status(repo: Repo, device_id: str) -> DeviceStatus:
    control, health = await asyncio.gather(repo.get_control(device_id), repo.get_health(device_id))
    return build_device_status(device_id, control, health, datetime.now(UTC))


@router.get("", response_model=list[DeviceStatus])
async def list_devices(
    _: auth.SessionUser = Depends(auth.require_role("viewer")), repo: Repo = Depends(get_repo)
):
    return [await _status(repo, d) for d in sorted(settings.allowed_devices)]


@router.get("/{device_id}/summary", response_model=Summary)
async def device_summary(
    device_id: str,
    range: SummaryRange = "7d",
    _: auth.SessionUser = Depends(auth.require_role("viewer")),
    repo: Repo = Depends(get_repo),
):
    _device_or_404(device_id)
    window, stats = await asyncio.gather(repo.events_window(device_id), repo.daily_stats(device_id))
    data = summary.build_summary(
        range,
        now=datetime.now(UTC),
        tz_name=settings.site_timezone,
        window_events=window.events,
        window_complete=window.complete,
        stats=stats,
    )
    return Summary(**data)


@router.get("/{device_id}/events", response_model=EventPage)
async def device_events(
    device_id: str,
    cursor: str | None = None,
    limit: int = Query(24, ge=1, le=100),
    clase: str | None = None,
    min_conf: float | None = Query(None, ge=0, le=1),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    include_simulated: bool = False,
    _: auth.SessionUser = Depends(auth.require_role("viewer")),
    repo: Repo = Depends(get_repo),
):
    _device_or_404(device_id)
    flt = EventFilters(
        clase=clase,
        min_conf=min_conf,
        from_=_aware(from_),
        to=_aware(to),
        include_simulated=include_simulated,
    )
    items, next_cursor = await repo.list_events(device_id, flt, cursor, limit)
    now = datetime.now(UTC)
    return EventPage(items=[event_summary(e, now) for e in items], next_cursor=next_cursor)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.put("/{device_id}/desired-state", response_model=DeviceStatus)
async def set_desired_state(
    device_id: str,
    body: DesiredStateBody,
    user: auth.SessionUser = Depends(auth.require_role("operator")),
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
):
    _device_or_404(device_id)
    await repo.set_desired_state(device_id, body.state, user.email)
    audit.write(user.email, "desired_state", device_id, body.state)
    return await _status(repo, device_id)
