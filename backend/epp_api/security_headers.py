from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .settings import settings


def _ws_origin(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :]
    return ""


def build_csp(public_base_url: str) -> str:
    # Los dominios de Google Identity Services son los que exige su documentación para la CSP.
    connect = ["'self'"]
    ws = _ws_origin(public_base_url)
    if ws:
        connect.append(ws)
    connect.append("https://accounts.google.com/gsi/")
    return (
        "default-src 'self'; "
        "script-src 'self' https://accounts.google.com/gsi/client; "
        "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; "
        "frame-src https://accounts.google.com/gsi/; "
        f"connect-src {' '.join(connect)}; "
        "img-src 'self' data: blob: https://storage.googleapis.com https://*.googleusercontent.com; "
        "font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._headers = {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": build_csp(settings.public_base_url),
        }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for k, v in self._headers.items():
            response.headers.setdefault(k, v)
        return response
