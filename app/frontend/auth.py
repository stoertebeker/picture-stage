"""Frontend auth routes: login, signup, verify-email, logout."""

import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_user_from_cookie
from app.auth.passwords import hash_password, hash_token, verify_password, verify_token
from app.auth.tokens import create_access_token, generate_verification_token
from app.db.models import LOGIN_ALLOWED_STATUSES, PendingSignup, User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates
from app.notifications.service import notify_admins_signup
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend-auth"])


def _csrf_from_request(request: Request) -> str:
    """Read CSRF token from cookie for template rendering."""
    return request.cookies.get("csrf_token", "")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    user = await get_user_from_cookie(request, db)
    if user is not None and user.status in LOGIN_ALLOWED_STATUSES:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"request": request, "csrf_token": _csrf_from_request(request), "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login_submit(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))

    if not email or not password:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "request": request,
                "csrf_token": _csrf_from_request(request),
                "error": "auth.email_password_required",
            },
            status_code=422,
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"request": request, "csrf_token": _csrf_from_request(request), "error": "auth.invalid_credentials"},
            status_code=401,
        )

    if user.status not in LOGIN_ALLOWED_STATUSES:
        error = "auth.account_disabled" if user.status == UserStatus.disabled else "auth.not_approved"
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "request": request,
                "csrf_token": _csrf_from_request(request),
                "error": error,
            },
            status_code=403,
        )

    access_token = create_access_token(str(user.id))
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=86400,
        path="/",
    )
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/signup.html",
        {"request": request, "csrf_token": _csrf_from_request(request), "error": None, "success": False},
    )


@router.post("/signup", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def signup_submit(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    ctx = {"request": request, "csrf_token": _csrf_from_request(request), "error": None, "success": False}

    if not email or not password:
        ctx["error"] = "auth.email_password_required"
        return templates.TemplateResponse(request, "auth/signup.html", ctx, status_code=422)

    if password != password_confirm:
        ctx["error"] = "auth.passwords_mismatch"
        return templates.TemplateResponse(request, "auth/signup.html", ctx, status_code=422)

    if len(password) < 8:
        ctx["error"] = "auth.password_too_short"
        return templates.TemplateResponse(request, "auth/signup.html", ctx, status_code=422)

    # Check for existing user
    existing_user = await db.execute(select(User).where(User.email == email))
    if existing_user.scalar_one_or_none() is not None:
        ctx["error"] = "auth.email_registered"
        return templates.TemplateResponse(request, "auth/signup.html", ctx, status_code=409)

    # Check for existing pending signup
    existing_signup = await db.execute(select(PendingSignup).where(PendingSignup.email == email))
    if existing_signup.scalar_one_or_none() is not None:
        ctx["error"] = "auth.signup_pending"
        return templates.TemplateResponse(request, "auth/signup.html", ctx, status_code=409)

    verification_token = generate_verification_token()
    token_hash, token_salt = hash_token(verification_token)

    pending = PendingSignup(
        email=email,
        password_hash=hash_password(password),
        verification_token_hash=token_hash,
        verification_token_salt=token_salt,
    )
    db.add(pending)
    await db.commit()

    try:
        await notify_admins_signup(email, db)
    except Exception:
        logger.exception("Failed to send signup_pending notification to admins")

    # TODO: send verification email via SMTP
    ctx["success"] = True
    return templates.TemplateResponse(request, "auth/signup.html", ctx)


@router.post("/logout")
async def logout_page(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session", path="/")
    return response


@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email_page(request: Request, token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    result = await db.execute(select(PendingSignup))
    pending_signups = result.scalars().all()

    matched_signup: PendingSignup | None = None
    for signup in pending_signups:
        if verify_token(token, signup.verification_token_hash, signup.verification_token_salt):
            matched_signup = signup
            break

    if matched_signup is None:
        return templates.TemplateResponse(
            request,
            "auth/verify.html",
            {"request": request, "success": False, "error": "auth.invalid_verify_token"},
            status_code=404,
        )

    # Clear token so it can't be reused
    matched_signup.verification_token_hash = b""
    matched_signup.verification_token_salt = b""
    await db.commit()

    return templates.TemplateResponse(
        request,
        "auth/verify.html",
        {"request": request, "success": True, "error": None},
    )
