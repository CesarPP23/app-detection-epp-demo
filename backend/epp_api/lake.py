"""URLs firmadas de GCS (v4, 10 min) sin archivo de key, y estado de las capturas."""

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .settings import settings

log = logging.getLogger("lake")

_GS = re.compile(r"^gs://([^/]+)/(.+)$")
SIGNED_URL_MINUTES = 10
CACHE_SECONDS = 9 * 60  # menos que la vigencia, para que una URL cacheada nunca esté vencida


def capture_status(stored: str | None, end_ts: datetime, now: datetime | None = None) -> str:
    """El estado guardado, salvo que la imagen ya haya salido de la retención (se calcula)."""
    status = stored or "missing"
    if status == "ok":
        limit = (now or datetime.now(UTC)) - timedelta(days=settings.lake_image_retention_days)
        if end_ts < limit:
            return "expired"
    return status


def split_uri(uri: str) -> tuple[str, str]:
    m = _GS.match(uri or "")
    if not m or m.group(1) != settings.lake_bucket:
        raise ValueError("URI fuera del bucket del lake")
    return m.group(1), m.group(2)


def _default_signer(uri: str) -> str:
    import google.auth
    from google.auth.transport.requests import Request
    from google.cloud import storage

    bucket, path = split_uri(uri)
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not creds.valid:
        creds.refresh(Request())
    client = storage.Client(project=settings.gcp_project_id, credentials=creds)
    # Firma con signBlob de la SA de runtime (sin archivo de key): se pasa el bearer de las credenciales.
    sign_args = {"service_account_email": settings.runtime_sa_email, "access_token": creds.token}
    return (
        client.bucket(bucket)
        .blob(path)
        .generate_signed_url(
            version="v4", expiration=timedelta(minutes=SIGNED_URL_MINUTES), method="GET", **sign_args
        )
    )


class Lake:
    def __init__(
        self, signer: Callable[[str], str] | None = None, clock: Callable[[], float] = time.monotonic
    ):
        self._signer = signer or _default_signer
        self._clock = clock
        self._cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def sign(self, uri: str) -> str:
        """Bloqueante (red): llamar con asyncio.to_thread. Cachea 9 min por objeto."""
        split_uri(uri)  # nunca firmar algo fuera del bucket del lake
        with self._lock:
            hit = self._cache.get(uri)
            if hit and self._clock() - hit[0] < CACHE_SECONDS:
                return hit[1]
        url = self._signer(uri)
        with self._lock:
            self._cache[uri] = (self._clock(), url)
        return url


_lake: Lake | None = None


def get_lake() -> Lake:
    global _lake
    if _lake is None:
        _lake = Lake()
    return _lake
