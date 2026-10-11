"""Configuración del borde, validada al arrancar (fail-closed)."""
import logging
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("config")

_DEVICE_ID_RE = re.compile(r"^[a-z0-9-]{3,40}$")
_CAMERA_RE = re.compile(r"^(auto|picamera2|usb:\d+|file:.+)$")


class ConfigError(RuntimeError):
    pass


def _bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass(frozen=True)
class Settings:
    device_id: str
    gcp_project_id: str
    control_database_id: str
    detections_database_id: str
    google_application_credentials: Path
    lake_bucket: str
    orchestrator_host: str
    device_token: str
    camera_source: str
    allow_test_video: bool
    capture_width: int
    capture_height: int
    capture_fps: int
    model_path: Path
    model_imgsz: int
    inference_fps: float
    confidence_threshold: float
    iou_threshold: float
    min_seconds_to_confirm: float
    min_frames_to_confirm: int
    session_gap_seconds: float
    capture_jpeg_quality: int
    thumb_width: int
    thumb_jpeg_quality: int
    stream_fps: float
    stream_width: int
    stream_jpeg_quality: int
    auto_stream_on_detection: bool
    heartbeat_seconds: int
    control_safety_poll_seconds: int
    default_desired_state: str
    data_dir: Path
    outbox_max_bytes: int
    site_timezone: str
    healthcheck_url: str
    log_level: str
    log_to_file: bool

    @property
    def uplink_url(self) -> str:
        return f"wss://{self.orchestrator_host}/ws/video/{self.device_id}"

    @property
    def is_simulated(self) -> bool:
        return self.camera_source.startswith("file:")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        errors: list[str] = []

        def get(name: str, default: str | None = None) -> str:
            v = e.get(name)
            if v is None or v == "":
                if default is None:
                    errors.append(f"falta la variable obligatoria {name}")
                    return ""
                return default
            return v

        def num(name: str, default: str, cast, lo=None, hi=None):
            raw = get(name, default)
            try:
                v = cast(raw)
            except ValueError:
                errors.append(f"{name}={raw!r} no es un número válido")
                return cast(default)
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                errors.append(f"{name}={v} fuera de rango [{lo}, {hi}]")
            return v

        device_id = get("DEVICE_ID")
        if device_id and not _DEVICE_ID_RE.match(device_id):
            errors.append("DEVICE_ID debe cumplir ^[a-z0-9-]{3,40}$")

        host = get("ORCHESTRATOR_HOST")
        if "://" in host or "/" in host:
            errors.append("ORCHESTRATOR_HOST va sin esquema ni ruta (solo el host)")

        token = get("DEVICE_TOKEN")
        if token and len(token) < 32:
            errors.append("DEVICE_TOKEN debe tener al menos 32 caracteres")

        camera = get("CAMERA_SOURCE", "auto")
        if not _CAMERA_RE.match(camera):
            errors.append("CAMERA_SOURCE debe ser auto | picamera2 | usb:<idx> | file:<ruta>")
        allow_test = _bool(get("ALLOW_TEST_VIDEO", "false"))
        if camera.startswith("file:") and not allow_test:
            errors.append("CAMERA_SOURCE=file:... requiere ALLOW_TEST_VIDEO=true (nunca en producción)")

        imgsz = num("MODEL_IMGSZ", "416", int, 32, 1280)
        if imgsz % 32:
            errors.append("MODEL_IMGSZ debe ser múltiplo de 32")

        desired = get("DEFAULT_DESIRED_STATE", "running")
        if desired not in {"running", "stopped"}:
            errors.append("DEFAULT_DESIRED_STATE debe ser running o stopped")

        s = cls(
            device_id=device_id,
            gcp_project_id=get("GCP_PROJECT_ID", "epp-detection-v1"),
            control_database_id=get("CONTROL_DATABASE_ID", "(default)"),
            detections_database_id=get("DETECTIONS_DATABASE_ID", "dbraspberry"),
            google_application_credentials=Path(
                get("GOOGLE_APPLICATION_CREDENTIALS", "/etc/epp-edge/gcp-key.json")
            ),
            lake_bucket=get("LAKE_BUCKET", "epp-detection-v1-lake"),
            orchestrator_host=host,
            device_token=token,
            camera_source=camera,
            allow_test_video=allow_test,
            capture_width=num("CAPTURE_WIDTH", "1280", int, 160, 4096),
            capture_height=num("CAPTURE_HEIGHT", "720", int, 120, 4096),
            capture_fps=num("CAPTURE_FPS", "30", int, 1, 120),
            model_path=Path(get("MODEL_PATH", "/var/lib/epp-edge/models/best_ncnn_model")),
            model_imgsz=imgsz,
            inference_fps=num("INFERENCE_FPS", "5", float, 0.1, 60),
            confidence_threshold=num("CONFIDENCE_THRESHOLD", "0.5", float, 0.0, 1.0),
            iou_threshold=num("IOU_THRESHOLD", "0.45", float, 0.0, 1.0),
            min_seconds_to_confirm=num("MIN_SECONDS_TO_CONFIRM", "1.0", float, 0.0),
            min_frames_to_confirm=num("MIN_FRAMES_TO_CONFIRM", "3", int, 1),
            session_gap_seconds=num("SESSION_GAP_SECONDS", "4.0", float, 0.0),
            capture_jpeg_quality=num("CAPTURE_JPEG_QUALITY", "90", int, 1, 100),
            thumb_width=num("THUMB_WIDTH", "480", int, 64, 1920),
            thumb_jpeg_quality=num("THUMB_JPEG_QUALITY", "75", int, 1, 100),
            stream_fps=num("STREAM_FPS", "8", float, 0.5, 30),
            stream_width=num("STREAM_WIDTH", "640", int, 160, 1920),
            stream_jpeg_quality=num("STREAM_JPEG_QUALITY", "60", int, 1, 100),
            auto_stream_on_detection=_bool(get("AUTO_STREAM_ON_DETECTION", "false")),
            heartbeat_seconds=num("HEARTBEAT_SECONDS", "300", int, 10),
            control_safety_poll_seconds=num("CONTROL_SAFETY_POLL_SECONDS", "300", int, 10),
            default_desired_state=desired,
            data_dir=Path(get("DATA_DIR", "/var/lib/epp-edge")),
            outbox_max_bytes=num("OUTBOX_MAX_BYTES", "5000000000", int, 1_000_000),
            site_timezone=get("SITE_TIMEZONE", "America/Lima"),
            healthcheck_url=e.get("HEALTHCHECK_URL", "") or "",
            log_level=get("LOG_LEVEL", "INFO").upper(),
            log_to_file=_bool(get("LOG_TO_FILE", "false")),
        )
        if errors:
            raise ConfigError("Configuración inválida:\n  - " + "\n  - ".join(errors))
        return s

    def warn_if_credentials_too_open(self) -> None:
        """Advierte si la key de la SA es legible por 'otros' (más abierto que 0640)."""
        try:
            mode = stat.S_IMODE(self.google_application_credentials.stat().st_mode)
        except OSError:
            return
        if mode & 0o007:
            log.warning(
                "%s tiene permisos %o, más abiertos que 0640", self.google_application_credentials, mode
            )
