from fastapi import APIRouter, Depends, Request, Response

from .. import auth
from ..audit import Audit, get_audit
from ..errors import ApiError
from ..repo import Repo, get_repo
from ..schemas import GoogleLogin, Me

router = APIRouter(tags=["auth"])


def _me(user: auth.SessionUser) -> Me:
    return Me(email=user.email, name=user.name, picture=user.picture, role=user.role)  # type: ignore[arg-type]


@router.post("/google", response_model=Me)
async def login_google(
    body: GoogleLogin,
    request: Request,
    response: Response,
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
):
    auth.check_csrf(request)
    if auth.settings.auth_mode == "dev":
        user = auth.dev_user()
        auth.set_session_cookie(response, user)
        return _me(user)
    auth.check_login_rate(auth.client_ip(request))
    try:
        info = await auth.run_blocking(auth.verify_google_token, body.credential)
    except ValueError as e:
        raise ApiError(401, "credencial_invalida", "No se pudo verificar tu cuenta de Google.") from e
    email = info["email"].strip().lower()
    record = await repo.get_user(email)
    if not record or not record.get("active") or record.get("role") not in auth.ROLE_ORDER:
        audit.write(email, "login_denied", None)
        raise ApiError(
            403, "acceso_denegado", f"Tu cuenta ({email}) no tiene acceso. Pide acceso al administrador."
        )
    user = auth.SessionUser(
        email=email, role=record["role"], name=info.get("name"), picture=info.get("picture")
    )
    auth.set_session_cookie(response, user)
    audit.write(email, "login", None)
    return _me(user)


@router.post("/logout", status_code=204)
async def logout(request: Request):
    auth.check_csrf(request)
    response = Response(status_code=204)
    auth.clear_session_cookie(response)
    return response


@router.get("/me", response_model=Me)
async def me(user: auth.SessionUser = Depends(auth.current_user)):
    return _me(user)
