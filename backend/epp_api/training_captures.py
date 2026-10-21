"""Construye el ZIP de una sesión de captura de entrenamiento, bajo demanda y en streaming.

No hay outbox ni cola: el admin pide el ZIP, se lista el prefijo de la sesión en el lake y se
envía foto por foto. Nunca se arma entero en memoria: Cloud Run rechaza respuestas no
streameadas de más de 32 MiB ("Response size was too large") y una sesión de ~2000 fotos pesa
~175 MB, más que los 512 MiB de la instancia con margen. `lister` es inyectable para que las
pruebas no necesiten GCS real, igual que `Lake(signer=...)` en `lake.py`.
"""

import zipfile
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor

from .settings import settings

MAX_OBJECTS = 5000  # el borde corta solo a los max_duration_s (1h por defecto): a 1 fps son hasta
# 3600 fotos por sesión — el tope de acá debe quedar por encima de eso con margen, si no, una
# sesión normal de duración completa rebotaría siempre con "sesión demasiado grande".
PREFETCH = 8  # descargas de GCS en vuelo: acota la memoria a ~8 fotos y evita ir de a una

Entry = tuple[str, Callable[[], bytes]]  # (nombre del objeto, función que baja sus bytes)


class SessionTooLargeError(Exception):
    """La sesión tiene más objetos de los que es razonable zippear en una sola request."""


def training_capture_prefix(device_id: str, session_id: str) -> str:
    return f"raw/training_captures/device={device_id}/session={session_id}/"


def _default_lister(prefix: str) -> Iterable[Entry]:
    from google.cloud import storage

    client = storage.Client(project=settings.gcp_project_id)
    for blob in client.list_blobs(settings.lake_bucket, prefix=prefix):
        yield blob.name, blob.download_as_bytes


class _Sink:
    """Destino no buscable para zipfile: acumula lo escrito hasta que el generador lo entrega."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def write(self, b: bytes) -> int:
        self._buf += b
        return len(b)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


class CaptureArchiver:
    """Bloqueante (red): `list_session` con asyncio.to_thread; `iter_zip` lo itera Starlette
    en su threadpool."""

    def __init__(self, lister: Callable[[str], Iterable[Entry]] | None = None) -> None:
        self._lister = lister or _default_lister

    def list_session(self, prefix: str, max_objects: int = MAX_OBJECTS) -> list[Entry]:
        """Solo metadatos: valida tamaño y existencia antes de empezar a responder."""
        entries: list[Entry] = []
        for entry in self._lister(prefix):
            entries.append(entry)
            if len(entries) > max_objects:
                raise SessionTooLargeError(
                    f"La sesión tiene más de {max_objects} fotos; bájala con "
                    f"'gcloud storage cp -r gs://{settings.lake_bucket}/{prefix} .' en vez de "
                    "descargarla desde el dashboard."
                )
        if not entries:
            raise FileNotFoundError(prefix)
        return entries

    def iter_zip(self, entries: list[Entry]) -> Iterator[bytes]:
        # ZIP_STORED: los JPEG ya vienen comprimidos, deflate solo gastaría CPU.
        sink = _Sink()
        pending = iter(entries)
        with (
            ThreadPoolExecutor(PREFETCH) as pool,
            zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as zf,  # type: ignore[arg-type]
        ):
            window: deque[tuple[str, Future[bytes]]] = deque()

            def refill() -> None:
                while len(window) < PREFETCH and (nxt := next(pending, None)):
                    window.append((nxt[0], pool.submit(nxt[1])))

            refill()
            while window:
                name, fut = window.popleft()
                zf.writestr(name.rsplit("/", 1)[-1], fut.result())
                refill()
                yield sink.take()
        yield sink.take()  # directorio central del ZIP, escrito al cerrar


_archiver: CaptureArchiver | None = None


def get_archiver() -> CaptureArchiver:
    global _archiver
    if _archiver is None:
        _archiver = CaptureArchiver()
    return _archiver
