import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

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
    TrainingCaptureStarted,
    TrainingSessionPage,
    TrainingSessionSummary,
)
from ..settings import settings
from ..training_captures import (
    CaptureArchiver,
    SessionTooLargeError,
    get_archiver,
    training_capture_prefix,
)

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


def training_session_summary(d: dict) -> TrainingSessionSummary:
    return TrainingSessionSummary(
        session_id=d.get("session_id", d.get("id", "")),
        device_id=d.get("device_id", ""),
        started_at=d["started_at"],
        started_by=d.get("started_by", ""),
        ended_at=d.get("ended_at"),
        frame_count=d.get("frame_count") or 0,
        status=d.get("status", "capturing"),  # type: ignore[arg-type]
        closed_reason=d.get("closed_reason"),
    )


@router.post("/{device_id}/training-captures", response_model=TrainingCaptureStarted)
async def start_training_capture(
    device_id: str,
    user: auth.SessionUser = Depends(auth.require_role("operator")),
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
):
    """Pide al borde que empiece a subir fotos crudas para reentrenamiento. El reentrenamiento
    en sí se hace en dev, fuera de este sistema; esto solo junta el dataset."""
    _device_or_404(device_id)
    session_id, started_at = await repo.start_training_capture(device_id, user.email)
    audit.write(user.email, "training_capture_start", device_id, session_id)
    return TrainingCaptureStarted(session_id=session_id, started_at=started_at)


@router.post("/{device_id}/training-captures/{session_id}/stop", status_code=204)
async def stop_training_capture(
    device_id: str,
    session_id: str,
    user: auth.SessionUser = Depends(auth.require_role("operator")),
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
) -> None:
    _device_or_404(device_id)
    await repo.stop_training_capture(device_id, session_id, user.email)
    audit.write(user.email, "training_capture_stop", device_id, session_id)


@router.get("/{device_id}/training-captures", response_model=TrainingSessionPage)
async def list_training_captures(
    device_id: str,
    cursor: str | None = None,
    limit: int = Query(24, ge=1, le=100),
    _: auth.SessionUser = Depends(auth.require_role("viewer")),
    repo: Repo = Depends(get_repo),
):
    _device_or_404(device_id)
    items, next_cursor = await repo.list_training_sessions(device_id, cursor, limit)
    return TrainingSessionPage(items=[training_session_summary(d) for d in items], next_cursor=next_cursor)


@router.get("/{device_id}/training-captures/{session_id}/download")
async def download_training_capture(
    device_id: str,
    session_id: str,
    user: auth.SessionUser = Depends(auth.require_role("admin")),
    archiver: CaptureArchiver = Depends(get_archiver),
    audit: Audit = Depends(get_audit),
):
    """Zip armado al vuelo con las fotos crudas de la sesión, enviado en streaming (sesiones de
    cientos de MB). Rol admin: misma sensibilidad que `EventDetail.lake_uri` (son fotos de
    operarios)."""
    _device_or_404(device_id)
    prefix = training_capture_prefix(device_id, session_id)
    try:
        entries = await asyncio.to_thread(archiver.list_session, prefix)
    except SessionTooLargeError as err:
        raise ApiError(400, "sesion_demasiado_grande", str(err)) from err
    except FileNotFoundError as err:
        raise ApiError(404, "sesion_no_encontrada", "No hay fotos para esa sesión.") from err
    audit.write(user.email, "training_capture_download", device_id, session_id)
    return StreamingResponse(
        archiver.iter_zip(entries),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.zip"'},
    )
