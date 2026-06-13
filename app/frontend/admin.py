import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service
from app.admin.service import AdminActionError
from app.auth.dependencies import get_user_from_cookie
from app.db.models import PendingSignup, User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates
from app.i18n import t as translate
from app.storage.base import StorageBackend
from app.storage.dependencies import get_storage

router = APIRouter(tags=["frontend-admin"])

# Maps a semantic UI action to (target status, success-toast i18n key) so each
# action produces an unambiguous toast (e.g. enable vs. demote both -> active).
_STATUS_ACTIONS: dict[str, tuple[UserStatus, str]] = {
    "promote": (UserStatus.admin, "admin.user_promoted"),
    "demote": (UserStatus.active, "admin.user_demoted"),
    "disable": (UserStatus.disabled, "admin.user_disabled"),
    "enable": (UserStatus.active, "admin.user_enabled"),
}


def _locale(request: Request) -> str:
    return getattr(request.state, "locale", "de") if hasattr(request, "state") else "de"


def _toast_only(message: str, kind: str = "success") -> HTMLResponse:
    """Response that shows a toast without swapping the target (HX-Reswap: none)."""
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"kind": kind, "message": message}})
    resp.headers["HX-Reswap"] = "none"
    return resp


async def require_admin_page(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await get_user_from_cookie(request, db)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if user.status != UserStatus.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/admin/signups", response_class=HTMLResponse)
async def admin_pending_signups(
    request: Request,
    user: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(PendingSignup).order_by(PendingSignup.requested_at.desc()))
    signups = list(result.scalars().all())
    csrf_token = request.cookies.get("csrf_token", "")
    return templates.TemplateResponse(
        request,
        "admin/pending.html",
        {
            "request": request,
            "user": user,
            "signups": signups,
            "csrf_token": csrf_token,
        },
    )


@router.post("/admin/approve/{signup_id}", response_class=HTMLResponse)
async def admin_approve_signup(
    signup_id: int,
    request: Request,
    _admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(PendingSignup).where(PendingSignup.id == signup_id))
    signup = result.scalar_one_or_none()
    if signup is None:
        raise HTTPException(status_code=404, detail="Pending signup not found")

    existing = await db.execute(select(User).where(User.email == signup.email))
    if existing.scalar_one_or_none() is not None:
        await db.delete(signup)
        await db.commit()
        raise HTTPException(status_code=409, detail="User with this email already exists")

    user = User(
        email=signup.email,
        password_hash=signup.password_hash,
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.delete(signup)
    await db.commit()

    return HTMLResponse("")


@router.post("/admin/reject/{signup_id}", response_class=HTMLResponse)
async def admin_reject_signup(
    signup_id: int,
    request: Request,
    _admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(PendingSignup).where(PendingSignup.id == signup_id))
    signup = result.scalar_one_or_none()
    if signup is None:
        raise HTTPException(status_code=404, detail="Pending signup not found")

    await db.delete(signup)
    await db.commit()

    return HTMLResponse("")


@router.get("/admin/nav-badge", response_class=HTMLResponse)
async def admin_nav_badge(
    request: Request,
    _admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Lazy-loaded pending-signups badge for the admin nav (empty when none)."""
    count = await service.count_pending_signups(db)
    return templates.TemplateResponse(request, "admin/_nav_badge.html", {"request": request, "count": count})


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    user: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    rows, _total = await service.list_users(db, page=1, per_page=200, status_filter=None)
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "rows": rows,
            "csrf_token": request.cookies.get("csrf_token", ""),
        },
    )


@router.post("/admin/users/{user_id}/status/{action}", response_class=HTMLResponse)
async def admin_change_user_status(
    user_id: uuid.UUID,
    action: str,
    request: Request,
    admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if action not in _STATUS_ACTIONS:
        raise HTTPException(status_code=404, detail="Unknown action")
    new_status, toast_key = _STATUS_ACTIONS[action]
    locale = _locale(request)

    try:
        target = await service.change_user_status(db, actor=admin, target_id=user_id, new_status=new_status)
    except AdminActionError as err:
        return _toast_only(translate(err.i18n_key, locale), "danger")

    galleries_count = await service.galleries_count(db, target.id)
    resp = templates.TemplateResponse(
        request,
        "admin/_user_row.html",
        {"request": request, "user": target, "current_user": admin, "galleries_count": galleries_count},
    )
    resp.headers["HX-Trigger"] = json.dumps(
        {"showToast": {"kind": "success", "message": translate(toast_key, locale, email=target.email)}}
    )
    return resp


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
async def admin_delete_user(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> HTMLResponse:
    locale = _locale(request)
    target = await db.get(User, user_id)
    email = target.email if target is not None else ""

    try:
        await service.delete_user(db, storage, actor=admin, target_id=user_id)
    except AdminActionError as err:
        return _toast_only(translate(err.i18n_key, locale), "danger")

    # Empty body + outerHTML swap removes the row; toast confirms.
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = json.dumps(
        {"showToast": {"kind": "success", "message": translate("admin.user_deleted_toast", locale, email=email)}}
    )
    return resp


@router.post("/admin/users/{user_id}/reset-password", response_class=HTMLResponse)
async def admin_reset_user_password(
    user_id: uuid.UUID,
    request: Request,
    new_password: str = Form(...),
    admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    locale = _locale(request)
    if len(new_password) < 8:
        return _toast_only(translate("admin.err_generic", locale), "danger")

    try:
        target = await service.reset_user_password(db, actor=admin, target_id=user_id, new_password=new_password)
    except AdminActionError as err:
        return _toast_only(translate(err.i18n_key, locale), "danger")

    return _toast_only(translate("admin.password_reset_done", locale, email=target.email), "success")


@router.post("/admin/users/{user_id}/gallery-limit", response_class=HTMLResponse)
async def admin_set_gallery_limit(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    locale = _locale(request)
    form = await request.form()
    raw = str(form.get("limit", "")).strip()
    # Empty string = clear override (back to global default).
    override: int | None = None
    if raw != "":
        try:
            override = int(raw)
        except ValueError:
            return _toast_only(translate("admin.err_invalid_limit", locale), "danger")

    try:
        target = await service.set_gallery_limit_override(db, actor=admin, target_id=user_id, override=override)
    except AdminActionError as err:
        return _toast_only(translate(err.i18n_key, locale), "danger")

    galleries_count = await service.galleries_count(db, target.id)
    resp = templates.TemplateResponse(
        request,
        "admin/_user_row.html",
        {"request": request, "user": target, "current_user": admin, "galleries_count": galleries_count},
    )
    msg = translate("admin.gallery_limit_toast", locale, email=target.email)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"kind": "success", "message": msg}})
    return resp
