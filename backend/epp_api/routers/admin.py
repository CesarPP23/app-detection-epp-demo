from fastapi import APIRouter, Depends, Query, Response

from .. import auth
from ..audit import Audit, get_audit
from ..errors import ApiError
from ..repo import Repo, get_repo
from ..schemas import AuditEntry, AuditPage, UserCreate, UserOut, UserPatch

router = APIRouter(tags=["admin"])
admin_only = auth.require_role("admin")


def _user_out(u: dict) -> UserOut:
    return UserOut(
        email=u["email"],
        role=u.get("role", "viewer"),
        active=bool(u.get("active")),
        created_by=u.get("created_by"),
        created_at=u.get("created_at"),
    )


def _is_active_admin(u: dict) -> bool:
    return bool(u.get("active")) and u.get("role") == "admin"


def _guard_last_admin(users: list[dict], target: dict, still_admin: bool) -> None:
    """Un cambio que deje al sistema sin admin activo se rechaza."""
    if _is_active_admin(target) and not still_admin:
        others = [u for u in users if u["email"] != target["email"] and _is_active_admin(u)]
        if not others:
            raise ApiError(409, "ultimo_admin", "No se puede dejar el sistema sin un administrador activo.")


async def _find(repo: Repo, email: str) -> tuple[list[dict], dict]:
    users = await repo.list_users()
    target = next((u for u in users if u["email"] == email.strip().lower()), None)
    if target is None:
        raise ApiError(404, "usuario_no_encontrado", "No existe ese usuario.")
    return users, target


@router.get("/users", response_model=list[UserOut])
async def list_users(_: auth.SessionUser = Depends(admin_only), repo: Repo = Depends(get_repo)):
    return [_user_out(u) for u in await repo.list_users()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    admin: auth.SessionUser = Depends(admin_only),
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
):
    user = await repo.create_user(body.email, body.role, admin.email)
    audit.write(admin.email, "user_create", user["email"], body.role)
    return _user_out(user)


@router.patch("/users/{email}", response_model=UserOut)
async def patch_user(
    email: str,
    body: UserPatch,
    admin: auth.SessionUser = Depends(admin_only),
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
):
    users, target = await _find(repo, email)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise ApiError(422, "solicitud_invalida", "No hay cambios que aplicar.")
    if target["email"] == admin.email:
        raise ApiError(409, "uno_mismo", "No puedes cambiar tu propio rol ni desactivarte.")
    new_role = changes.get("role", target.get("role"))
    new_active = changes.get("active", target.get("active"))
    _guard_last_admin(users, target, still_admin=bool(new_active) and new_role == "admin")
    await repo.update_user(target["email"], changes)
    audit.write(admin.email, "user_update", target["email"], ",".join(f"{k}={v}" for k, v in changes.items()))
    return _user_out({**target, **changes})


@router.delete("/users/{email}", status_code=204)
async def delete_user(
    email: str,
    admin: auth.SessionUser = Depends(admin_only),
    repo: Repo = Depends(get_repo),
    audit: Audit = Depends(get_audit),
):
    users, target = await _find(repo, email)
    if target["email"] == admin.email:
        raise ApiError(409, "uno_mismo", "No puedes eliminarte a ti mismo.")
    _guard_last_admin(users, target, still_admin=False)
    await repo.delete_user(target["email"])
    audit.write(admin.email, "user_delete", target["email"])
    return Response(status_code=204)


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    action: str | None = None,
    email: str | None = None,
    _: auth.SessionUser = Depends(admin_only),
    repo: Repo = Depends(get_repo),
):
    items, next_cursor = await repo.list_audit(cursor, action, email, limit)
    return AuditPage(
        items=[
            AuditEntry(
                id=d["id"],
                ts=d["ts"],
                email=d.get("email", ""),
                action=d.get("action", ""),
                target=d.get("target"),
                detail=d.get("detail"),
            )
            for d in items
        ],
        next_cursor=next_cursor,
    )
