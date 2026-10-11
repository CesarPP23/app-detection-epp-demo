import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse

from .. import auth
from ..audit import Audit, get_audit
from ..errors import ApiError
from ..lake import Lake, capture_status, get_lake
from ..repo import Repo, get_repo
from ..schemas import CaptureDetail, DetectionBox, EdgeInfo, EventDetail
from ..settings import settings
from .devices import event_summary

router = APIRouter(tags=["events"])


async def _event_or_404(repo: Repo, event_id: str) -> dict:
    e = await repo.get_event(event_id)
    if e is None or e.get("device_id") not in settings.allowed_devices:
        raise ApiError(404, "evento_no_encontrado", "No existe ese evento.")
    return e


@router.get("/{event_id}", response_model=EventDetail)
async def event_detail(
    event_id: str,
    user: auth.SessionUser = Depends(auth.require_role("viewer")),
    repo: Repo = Depends(get_repo),
):
    e = await _event_or_404(repo, event_id)
    base = event_summary(e, datetime.now(UTC))
    cap = e.get("capture") or {}
    available = base.capture_status == "ok"
    return EventDetail(
        **base.model_dump(),
        schema_version=e.get("schema_version"),
        event_type=e.get("event_type"),
        device_id=e["device_id"],
        closed_reason=e.get("closed_reason"),
        capture=CaptureDetail(
            status=base.capture_status,
            captured_at=cap.get("captured_at"),
            width=cap.get("width"),
            height=cap.get("height"),
            bytes=cap.get("bytes"),
            frame_seq=cap.get("frame_seq"),
            confidence_at_capture=cap.get("confidence_at_capture"),
            sha256=cap.get("sha256"),
        ),
        detections_at_capture=[DetectionBox(**d) for d in e.get("detections_at_capture") or []],
        edge=EdgeInfo(**{k: (e.get("edge") or {}).get(k) for k in EdgeInfo.model_fields}),
        original_url=f"/api/events/{event_id}/capture?kind=original" if available else None,
        lake_uri=cap.get("uri") if user.role == "admin" else None,
    )


@router.get("/{event_id}/capture")
async def event_capture(
    event_id: str,
    kind: Literal["thumb", "original"] = Query("thumb"),
    user: auth.SessionUser = Depends(auth.require_role("viewer")),
    repo: Repo = Depends(get_repo),
    lake: Lake = Depends(get_lake),
    audit: Audit = Depends(get_audit),
):
    """302 a una URL firmada de GCS (10 min). La redirección se puede cachear 9 min en el navegador."""
    e = await _event_or_404(repo, event_id)
    cap = e.get("capture") or {}
    status = capture_status(cap.get("status"), e["end_ts"])
    if status == "expired":
        raise ApiError(410, "captura_expirada", "Captura eliminada por política de retención (60 días).")
    if status != "ok":
        raise ApiError(404, "sin_captura", "Este evento no tiene captura disponible.")
    uri = cap.get("uri") if kind == "original" else cap.get("thumb_uri")
    if not uri:
        raise ApiError(404, "sin_captura", "Este evento no tiene captura disponible.")
    try:
        url = await asyncio.to_thread(lake.sign, uri)
    except ValueError as err:
        raise ApiError(404, "sin_captura", "Este evento no tiene captura disponible.") from err
    if kind == "original":
        audit.write(user.email, "capture_original", event_id)
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=540"})
