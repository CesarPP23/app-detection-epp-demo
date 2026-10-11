import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, relay
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
app.include_router(relay.router)  # /ws/video/{device}
# Fases siguientes: live, routers.auth/devices/events/admin


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
    return FileResponse(index, headers={"Cache-Control": "no-store"})
