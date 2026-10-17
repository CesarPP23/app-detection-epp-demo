import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, relay
from .errors import install_error_handlers
from .routers import admin as admin_router
from .routers import auth as auth_router
from .routers import devices as devices_router
from .routers import events as events_router
from .security_headers import SecurityHeadersMiddleware
from .settings import settings

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class ImmutableStaticFiles(StaticFiles):
    """Assets con hash en el nombre: caché de 1 año e immutable."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app = FastAPI(
    title="EPP Monitor API",
    version=__version__,
    docs_url=None if settings.on_cloud_run else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.on_cloud_run else "/api/openapi.json",
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)  # JSON; los JPEG no se comprimen
install_error_handlers(app)
app.include_router(relay.router)  # /ws/video/{device}
try:  # live.py (WebSocket al navegador + lease) lo entrega 3-B
    from . import live
except ImportError:  # pragma: no cover
    live = None
if live is not None:
    app.include_router(live.router)  # /api/devices/{id}/live
app.include_router(auth_router.router, prefix="/api/auth")
app.include_router(devices_router.router, prefix="/api/devices")
app.include_router(events_router.router, prefix="/api/events")
app.include_router(admin_router.router, prefix="/api/admin")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": __version__}


# SPA: assets con hash => caché de 1 año e immutable; index.html => no-store; rutas desconocidas
# fuera de /api y /ws => index.html (routing del lado del cliente).
_static = Path(settings.static_dir)
if (_static / "assets").is_dir():
    app.mount("/assets", ImmutableStaticFiles(directory=_static / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    index = _static / "index.html"
    if path.startswith(("api/", "ws/")) or not index.is_file():
        raise HTTPException(404)
    if path:
        candidate = (_static / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_static.resolve()):
            return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
    return FileResponse(index, headers={"Cache-Control": "no-store"})
