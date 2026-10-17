"""Configuración fail-closed: si falta un valor obligatorio o es inválido, el servicio no arranca."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError as e:
        raise RuntimeError(f"{name} debe ser un entero") from e


def _secret(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise RuntimeError(f"{name} es obligatoria y debe tener al menos 32 caracteres")
    return value


@dataclass(frozen=True)
class Settings:
    device_token: str
    session_secret: str
    allowed_devices: frozenset[str]
    auth_mode: str
    on_cloud_run: bool
    google_oauth_client_id: str
    public_base_url: str
    gcp_project_id: str
    control_database_id: str
    detections_database_id: str
    lake_bucket: str
    runtime_sa_email: str
    max_frame_bytes: int
    publisher_idle_seconds: int
    live_max_fps: int
    max_live_viewers: int
    lease_seconds: int
    session_max_hours: int
    cache_ttl_seconds: int
    site_timezone: str
    static_dir: str
    log_level: str
    lake_image_retention_days: int

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        on_cloud_run = bool(env("K_SERVICE"))
        auth_mode = env("AUTH_MODE", "google")
        if auth_mode not in ("google", "dev"):
            raise RuntimeError("AUTH_MODE debe ser 'google' o 'dev'")
        if auth_mode == "dev" and on_cloud_run:
            raise RuntimeError("AUTH_MODE=dev no está permitido en Cloud Run")
        google_client_id = env("GOOGLE_OAUTH_CLIENT_ID", "")
        if auth_mode == "google" and not google_client_id:
            raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID es obligatoria con AUTH_MODE=google")
        public_base_url = env("PUBLIC_BASE_URL", "").rstrip("/")
        if on_cloud_run and not public_base_url.startswith("https://"):
            raise RuntimeError("PUBLIC_BASE_URL (https) es obligatoria en Cloud Run")
        runtime_sa = env("RUNTIME_SA_EMAIL", "")
        if on_cloud_run and not runtime_sa:
            raise RuntimeError("RUNTIME_SA_EMAIL es obligatoria en Cloud Run")
        devices = frozenset(d.strip() for d in env("ALLOWED_DEVICES", "").split(",") if d.strip())
        if not devices:
            raise RuntimeError("ALLOWED_DEVICES es obligatoria (lista separada por comas)")
        values = {
            "device_token": _secret("DEVICE_TOKEN"),
            "session_secret": _secret("SESSION_SECRET"),
            "allowed_devices": devices,
            "auth_mode": auth_mode,
            "on_cloud_run": on_cloud_run,
            "google_oauth_client_id": google_client_id,
            "public_base_url": public_base_url,
            "gcp_project_id": env("GCP_PROJECT_ID", "epp-detection-v1"),
            "control_database_id": env("CONTROL_DATABASE_ID", "(default)"),
            "detections_database_id": env("DETECTIONS_DATABASE_ID", "dbraspberry"),
            "lake_bucket": env("LAKE_BUCKET", "epp-detection-v1-lake"),
            "runtime_sa_email": runtime_sa,
            "max_frame_bytes": _int("MAX_FRAME_BYTES", 400_000),
            "publisher_idle_seconds": _int("PUBLISHER_IDLE_SECONDS", 15),
            "live_max_fps": _int("LIVE_MAX_FPS", 10),
            "max_live_viewers": _int("MAX_LIVE_VIEWERS", 10),
            "lease_seconds": _int("LEASE_SECONDS", 90),
            "session_max_hours": _int("SESSION_MAX_HOURS", 12),
            "cache_ttl_seconds": _int("CACHE_TTL_SECONDS", 10),
            "site_timezone": env("SITE_TIMEZONE", "America/Lima"),
            "static_dir": env("STATIC_DIR", "/app/static"),
            "log_level": env("LOG_LEVEL", "INFO"),
            "lake_image_retention_days": _int("LAKE_IMAGE_RETENTION_DAYS", 60),
        }
        return cls(**values)


settings = Settings.from_env()
