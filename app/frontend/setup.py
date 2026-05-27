"""Initial admin setup: first visitor creates the admin account."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.db.models import User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates
from app.security.rate_limit import limiter

router = APIRouter(tags=["frontend-setup"])


async def _has_users(db: AsyncSession) -> bool:
    result = await db.execute(select(func.count()).select_from(User))
    return (result.scalar() or 0) > 0


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if await _has_users(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    csrf_token = request.cookies.get("csrf_token", "")
    return templates.TemplateResponse(
        request,
        "setup/index.html",
        {"request": request, "csrf_token": csrf_token, "error": None},
    )


@router.post("/setup", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def setup_submit(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    if await _has_users(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    csrf_token = request.cookies.get("csrf_token", "")
    ctx = {"request": request, "csrf_token": csrf_token, "error": None}

    if not email or not password:
        ctx["error"] = "Email and password are required."
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=422)

    if password != password_confirm:
        ctx["error"] = "Passwords do not match."
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=422)

    if len(password) < 8:
        ctx["error"] = "Password must be at least 8 characters."
        return templates.TemplateResponse(request, "setup/index.html", ctx, status_code=422)

    admin = User(
        email=email,
        password_hash=hash_password(password),
        status=UserStatus.admin,
        email_verified_at=datetime.now(UTC),
    )
    db.add(admin)
    await db.commit()

    return RedirectResponse(url="/login", status_code=303)
