"""Frontend auth routes: login, signup, verify-email, logout."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_user_from_cookie
from app.auth.passwords import hash_password, hash_token, verify_password, verify_token
from app.auth.tokens import create_access_token, generate_verification_token
from app.db.models import PendingSignup, User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates
from app.security.rate_limit import limiter

router = APIRouter(tags=["frontend-auth"])


def _csrf_from_request(request: Request) -> str:
    """Read CSRF token from cookie for template rendering."""
    return request.cookies.get("csrf_token", "")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    user = await get_user_from_cookie(request, db)
    if user is not None and user.status != UserStatus.pending:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "csrf_token": _csrf_from_request(request), "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login_submit(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))

    if not email or not password:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "csrf_token": _csrf_from_request(request),
             "error": "Email and password are required."},
            status_code=422,
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "csrf_token": _csrf_from_request(request), "error": "Invalid email or password."},
            status_code=401,
        )

    if user.status == UserStatus.pending:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "csrf_token": _csrf_from_request(request),
             "error": "Account not yet approved by admin."},
            status_code=403,
        )

    access_token = create_access_token(str(user.id))
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400,
        path="/",
    )
    return response


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
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
        ctx["error"] = "Email and password are required."
        return templates.TemplateResponse("auth/signup.html", ctx, status_code=422)

    if password != password_confirm:
        ctx["error"] = "Passwords do not match."
        return templates.TemplateResponse("auth/signup.html", ctx, status_code=422)

    if len(password) < 8:
        ctx["error"] = "Password must be at least 8 characters."
        return templates.TemplateResponse("auth/signup.html", ctx, status_code=422)

    # Check for existing user
    existing_user = await db.execute(select(User).where(User.email == email))
    if existing_user.scalar_one_or_none() is not None:
        ctx["error"] = "Email already registered."
        return templates.TemplateResponse("auth/signup.html", ctx, status_code=409)

    # Check for existing pending signup
    existing_signup = await db.execute(select(PendingSignup).where(PendingSignup.email == email))
    if existing_signup.scalar_one_or_none() is not None:
        ctx["error"] = "Signup already pending."
        return templates.TemplateResponse("auth/signup.html", ctx, status_code=409)

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

    # TODO: send verification email via SMTP
    ctx["success"] = True
    return templates.TemplateResponse("auth/signup.html", ctx)


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
            "auth/verify.html",
            {"request": request, "success": False, "error": "Invalid or expired verification token."},
            status_code=404,
        )

    # Clear token so it can't be reused
    matched_signup.verification_token_hash = b""
    matched_signup.verification_token_salt = b""
    await db.commit()

    return templates.TemplateResponse(
        "auth/verify.html",
        {"request": request, "success": True, "error": None},
    )
