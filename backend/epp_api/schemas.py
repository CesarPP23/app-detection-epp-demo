"""Contrato de la API (3.3). Es la fuente del OpenAPI y, por tanto, de schema.d.ts del frontend."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["viewer", "operator", "admin"]
DeviceState = Literal["detecting", "live", "paused", "offline", "camera_error"]
CaptureStatus = Literal["ok", "missing", "dropped_disk_full", "missing_legacy", "expired"]
SummaryRange = Literal["24h", "7d", "30d"]

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- Auth -------------------------------------------------------------------------------------


class GoogleLogin(BaseModel):
    credential: str = Field(min_length=10, max_length=4096)


class Me(BaseModel):
    email: str
    name: str | None = None
    picture: str | None = None
    role: Role


# --- Dispositivos -----------------------------------------------------------------------------


class DeviceHealth(BaseModel):
    inference_fps: float | None = None
    inference_ms_p50: float | None = None
    inference_ms_p95: float | None = None
    cpu_temp_c: float | None = None
    outbox_pending: int | None = None
    edge_version: str | None = None
    model_version: str | None = None
    camera_source: str | None = None


class DeviceStatus(BaseModel):
    id: str
    name: str
    state: DeviceState
    desired_state: Literal["running", "stopped"]
    last_seen: datetime | None = None
    streaming: bool = False
    camera_ok: bool | None = None
    health: DeviceHealth


class DesiredStateBody(BaseModel):
    state: Literal["running", "stopped"]


# --- Resumen ----------------------------------------------------------------------------------


class KpiValue(BaseModel):
    value: float | None = None
    previous: float | None = None


class TopClass(BaseModel):
    nombre_clase: str | None = None
    count: int = 0
    previous_nombre_clase: str | None = None


class Kpis(BaseModel):
    events: KpiValue
    epp_seconds: KpiValue
    avg_confidence: KpiValue
    capture_coverage: KpiValue
    top_class: TopClass


class SeriesBucket(BaseModel):
    bucket: datetime
    total: int
    by_class: dict[str, int]


class ClassCount(BaseModel):
    nombre_clase: str
    count: int


class SampleInfo(BaseModel):
    """Los KPI de confianza y cobertura salen de los eventos recientes en caché, no de todos."""

    events: int
    complete: bool


class Summary(BaseModel):
    range: SummaryRange
    timezone: str
    generated_at: datetime
    partial: bool = Field(
        description="True si el periodo actual no está cubierto del todo por los datos disponibles."
    )
    sample: SampleInfo
    kpis: Kpis
    series: list[SeriesBucket]
    by_class: list[ClassCount]


# --- Eventos ----------------------------------------------------------------------------------


class EventSummary(BaseModel):
    event_id: str
    clase_id: int | None = None
    nombre_clase: str
    confianza_promedio: float
    confianza_max: float
    frame_count: int | None = None
    start_ts: datetime
    end_ts: datetime
    duration_seconds: float
    thumb_url: str | None = None
    capture_status: CaptureStatus
    is_simulated: bool = False


class EventPage(BaseModel):
    items: list[EventSummary]
    next_cursor: str | None = None


class DetectionBox(BaseModel):
    clase_id: int
    nombre_clase: str
    confianza: float
    bbox_xyxy_norm: list[float]


class CaptureDetail(BaseModel):
    status: CaptureStatus
    captured_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
    frame_seq: int | None = None
    confidence_at_capture: float | None = None
    sha256: str | None = None


class EdgeInfo(BaseModel):
    edge_version: str | None = None
    model_version: str | None = None
    camera_source: str | None = None


class EventDetail(EventSummary):
    schema_version: int | None = None
    event_type: str | None = None
    device_id: str
    closed_reason: str | None = None
    capture: CaptureDetail
    detections_at_capture: list[DetectionBox] = []
    edge: EdgeInfo = EdgeInfo()
    original_url: str | None = None
    lake_uri: str | None = Field(default=None, description="Solo para el rol admin.")


# --- Administración ---------------------------------------------------------------------------


class UserOut(BaseModel):
    email: str
    role: Role
    active: bool
    created_by: str | None = None
    created_at: datetime | None = None


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    role: Role = "viewer"


class UserPatch(BaseModel):
    role: Role | None = None
    active: bool | None = None


class AuditEntry(BaseModel):
    id: str
    ts: datetime
    email: str
    action: str
    target: str | None = None
    detail: str | None = None


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_cursor: str | None = None
