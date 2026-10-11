"""Autenticación (3.1): ID token de Google -> cookie de sesión firmada. Roles, CSRF y WebSockets."""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Depends, Request, WebSocket
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .errors import ApiError
from .repo import Repo, get_repo
from .settings import settings

log = logging.getLogger("auth")

COOKIE = "epp_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "epp-web"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
LOGIN_ATTEMPTS = 10  # por IP y por minuto
LOGIN_WINDOW = 60.0

DEV_USER_EMAIL = "dev@localhost"


@dataclass(frozen=True)
class SessionUser:
    email: str
    role: str
    name: str | None = None
    picture: str | None = None


# --- Cookie de sesión -------------------------------------------------------------------------


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="epp-session")


def issue_cookie_value(user: SessionUser) -> str:
    payload = {"email": user.email, "role": user.role, "name": user.name, "picture": user.picture}
    return _serializer().dumps(payload)


def read_cookie_value(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        data = _serializer().loads(value, max_age=settings.session_max_hours * 3600)
    except BadSignature:  # incluye SignatureExpired
        return None
    return data if isinstance(data, dict) and data.get("email") else None


def set_session_cookie(response, user: SessionUser) -> None:
    response.set_cookie(
        COOKIE,
        issue_cookie_value(user),
        max_age=settings.session_max_hours * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")


# --- Verificación del ID token de Google ------------------------------------------------------


def verify_google_token(credential: str) -> dict:
    """Bloqueante (red a los certificados de Google en el primer uso). Lanza ValueError si no es válido."""
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    info = id_token.verify_oauth2_token(credential, g_requests.Request(), settings.google_oauth_client_id)
    if info.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError("Emisor inválido")
    if info.get("email_verified") is not True:
        raise ValueError("Correo no verificado")
    if not info.get("email"):
        raise ValueError("Falta el correo")
    return {"email": info["email"], "name": info.get("name"), "picture": info.get("picture")}


# --- Límite de intentos -----------------------------------------------------------------------

_attempts: dict[str, deque] = defaultdict(deque)


def check_login_rate(ip: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    q = _attempts[ip]
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    if len(q) >= LOGIN_ATTEMPTS:
        raise ApiError(
            429, "demasiados_intentos", "Demasiados intentos de inicio de sesión. Espera un minuto."
        )
    q.append(now)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


# --- Dependencias -----------------------------------------------------------------------------


def check_csrf(request: Request) -> None:
    """Toda request que muta exige la cabecera: sin CORS, otro origen no puede enviarla."""
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "csrf", "Falta la cabecera de seguridad de la solicitud.")


def dev_user() -> SessionUser:
    return SessionUser(email=DEV_USER_EMAIL, role="admin", name="Desarrollo")


async def resolve_user(cookie_value: str | None, repo: Repo) -> SessionUser | None:
    """Valida la cookie y revalida el rol contra dashboard_users (caché de 60 s en el repo)."""
    if settings.auth_mode == "dev":
        return dev_user()
    data = read_cookie_value(cookie_value)
    if data is None:
        return None
    record = await repo.get_user(data["email"])
    if not record or not record.get("active"):
        return None
    role = record.get("role")
    if role not in ROLE_ORDER:
        return None
    return SessionUser(email=data["email"], role=role, name=data.get("name"), picture=data.get("picture"))


async def current_user(request: Request, repo: Repo = Depends(get_repo)) -> SessionUser:
    check_csrf(request)
    user = await resolve_user(request.cookies.get(COOKIE), repo)
    if user is None:
        raise ApiError(401, "no_autenticado", "Inicia sesión para continuar.")
    return user


def require_role(minimum: str):
    async def dependency(user: SessionUser = Depends(current_user)) -> SessionUser:
        if ROLE_ORDER[user.role] < ROLE_ORDER[minimum]:
            raise ApiError(403, "sin_permiso", "No tienes permiso para hacer esto.")
        return user

    return dependency


# --- WebSocket del navegador ------------------------------------------------------------------


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    if settings.public_base_url and origin.rstrip("/") == settings.public_base_url:
        return True
    if settings.auth_mode == "dev":  # Vite en desarrollo
        host = urlsplit(origin).hostname
        return host in ("localhost", "127.0.0.1")
    return False


async def authenticate_ws(ws: WebSocket) -> SessionUser | None:
    """Cookie + Origin == PUBLIC_BASE_URL. Si falla, cierra con 1008 y devuelve None."""
    from . import repo as repo_module

    if not _origin_allowed(ws.headers.get("origin")):
        log.warning("WebSocket rechazado: Origin no permitido")
        await ws.close(code=1008)
        return None
    # Respeta dependency_overrides (tests) igual que las rutas HTTP.
    getter = ws.app.dependency_overrides.get(repo_module.get_repo, repo_module.get_repo)
    user = await resolve_user(ws.cookies.get(COOKIE), getter())
    if user is None:
        await ws.close(code=1008)
        return None
    return user


async def run_blocking(fn, *args):
    return await asyncio.to_thread(fn, *args)
