"""First administrator setup guarded by an operator-held capability."""

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.config import settings
from app.db.models import SetupState, User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates
from app.security.rate_limit import limiter

router = APIRouter(tags=["frontend-setup"])


async def _has_users(db: AsyncSession) -> bool:
    result = await db.execute(select(func.count()).select_from(User))
    return (result.scalar() or 0) > 0


def _configured_bootstrap_token() -> str | None:
    """Require a generated, URL-safe operator capability; never expose its value."""
    token = settings.bootstrap_token.get_secret_value()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token) or len(set(token)) < 16:
        return None
    return token


async def _setup_state(db: AsyncSession, *, lock: bool = False) -> SetupState:
    statement = select(SetupState).where(SetupState.id == 1)
    if lock:
        statement = statement.with_for_update()
    state = await db.scalar(statement)
    if state is None:
        # A missing migration/seed must never reopen public bootstrap.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return state


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if await _has_users(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if (await _setup_state(db)).completed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    csrf_token = request.cookies.get("csrf_token", "")
    configured = _configured_bootstrap_token() is not None
    return templates.TemplateResponse(
        request,
        "setup/index.html",
        {
            "request": request,
            "csrf_token": csrf_token,
            "error": None if configured else "setup.bootstrap_not_configured",
        },
        status_code=200 if configured else 503,
    )


@router.post("/setup", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def setup_submit(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if await _has_users(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    expected_token = _configured_bootstrap_token()
    if expected_token is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    form = await request.form()
    submitted_token = str(form.get("bootstrap_token", ""))
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    csrf_token = request.cookies.get("csrf_token", "")
    ctx = {"request": request, "csrf_token": csrf_token, "error": None}

    if not secrets.compare_digest(submitted_token.encode("utf-8"), expected_token.encode("ascii")):
        ctx["error"] = "setup.invalid_bootstrap_token"
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=403)

    if not email or not password:
        ctx["error"] = "auth.email_password_required"
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=422)

    if password != password_confirm:
        ctx["error"] = "auth.passwords_mismatch"
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=422)

    if len(password) < 8:
        ctx["error"] = "auth.password_too_short"
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=422)

    state = await _setup_state(db, lock=True)
    if state.completed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if await _has_users(db):
        # Also consume the claim if an older app version created the first user
        # while this process was waiting for the singleton row.
        state.completed = True
        await db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    admin = User(
        email=email,
        password_hash=hash_password(password),
        status=UserStatus.admin,
        email_verified_at=datetime.now(UTC),
    )
    # A transaction-local capability additionally prevents an old app binary
    # from inserting an administrator without following this setup protocol.
    await db.execute(text("SELECT set_config('picture_stage.admin_insert', 'bootstrap', true)"))
    db.add(admin)
    await db.flush()
    state.completed = True
    await db.commit()

    return RedirectResponse(url="/login", status_code=303)
