"""Construye el ZIP de una sesión de captura de entrenamiento, bajo demanda.

No hay outbox ni cola: el admin pide el ZIP, se lista el prefijo de la sesión en el lake y se
arma en memoria. `lister` es inyectable para que las pruebas no necesiten GCS real, igual que
`Lake(signer=...)` en `lake.py`.
"""

import io
import zipfile
from collections.abc import Callable, Iterable

from .settings import settings

MAX_OBJECTS = 5000  # el borde corta solo a los max_duration_s (1h por defecto): a 1 fps son hasta
# 3600 fotos por sesión — el tope de acá debe quedar por encima de eso con margen, si no, una
# sesión normal de duración completa rebotaría siempre con "sesión demasiado grande".


class SessionTooLargeError(Exception):
    """La sesión tiene más objetos de los que es razonable zippear en una sola request."""


def training_capture_prefix(device_id: str, session_id: str) -> str:
    return f"raw/training_captures/device={device_id}/session={session_id}/"


def _default_lister(prefix: str) -> Iterable[tuple[str, bytes]]:
    from google.cloud import storage

    client = storage.Client(project=settings.gcp_project_id)
    for blob in client.list_blobs(settings.lake_bucket, prefix=prefix):
        yield blob.name, blob.download_as_bytes()


class CaptureArchiver:
    """Bloqueante (red): llamar con asyncio.to_thread."""

    def __init__(self, lister: Callable[[str], Iterable[tuple[str, bytes]]] | None = None) -> None:
        self._lister = lister or _default_lister

    def build_zip(self, prefix: str, max_objects: int = MAX_OBJECTS) -> bytes:
        buf = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in self._lister(prefix):
                count += 1
                if count > max_objects:
                    raise SessionTooLargeError(
                        f"La sesión tiene más de {max_objects} fotos; bájala con "
                        f"'gsutil -m cp -r gs://{settings.lake_bucket}/{prefix} .' en vez de "
                        "descargarla desde el dashboard."
                    )
                zf.writestr(name.rsplit("/", 1)[-1], data)
        if count == 0:
            raise FileNotFoundError(prefix)
        return buf.getvalue()


_archiver: CaptureArchiver | None = None


def get_archiver() -> CaptureArchiver:
    global _archiver
    if _archiver is None:
        _archiver = CaptureArchiver()
    return _archiver
