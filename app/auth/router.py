import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.passwords import hash_password, hash_token, verify_password, verify_token
from app.auth.schemas import LocaleUpdate, LoginRequest, LoginResponse, SignupRequest, SignupResponse, UserResponse
from app.auth.tokens import create_access_token, generate_verification_token
from app.auth.utils import get_client_ip
from app.db.models import LOGIN_ALLOWED_STATUSES, PendingSignup, User, UserStatus
from app.db.session import get_db
from app.notifications.service import notify_admins_signup, send_verification_email
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Pre-computed bcrypt hash (rounds=12, same cost as a real password). Used to
# equalize login timing when the email doesn't exist: both handlers always run
# exactly one bcrypt verify, so an attacker can't distinguish a missing account
# from a wrong password by response time (account-enumeration guard, mirrors the
# signup path / picture-stage-42q).
_DUMMY_PASSWORD_HASH = hash_password("timing-equalization-decoy")


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def signup(request: Request, body: SignupRequest, db: AsyncSession = Depends(get_db)) -> SignupResponse:
    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )

    # Account-enumeration guard (picture-stage-42q): an already-registered email
    # (as User OR PendingSignup) must NOT be revealed. Return the same neutral
    # response as a fresh signup, create NO new PendingSignup, and never overwrite
    # an existing account/password (that would be an account-takeover vector).
    existing_user = await db.execute(select(User).where(User.email == body.email))
    existing_signup = await db.execute(select(PendingSignup).where(PendingSignup.email == body.email))
    if existing_user.scalar_one_or_none() is not None or existing_signup.scalar_one_or_none() is not None:
        # Best-effort timing equalization: spend the same bcrypt time a fresh
        # signup would, so existing vs. new isn't trivially distinguishable.
        hash_password(body.password)
        return SignupResponse(message="Signup received. Please verify your email.")

    verification_token = generate_verification_token()
    token_hash, token_salt = hash_token(verification_token)

    pending = PendingSignup(
        email=body.email,
        password_hash=hash_password(body.password),
        verification_token_hash=token_hash,
        verification_token_salt=token_salt,
        ip_address=get_client_ip(request),
    )
    db.add(pending)
    await db.commit()

    try:
        await notify_admins_signup(body.email, db)
    except Exception:
        logger.exception("Failed to send signup_pending notification to admins")

    try:
        await send_verification_email(body.email, verification_token, db)
    except Exception:
        logger.exception("Failed to send verification email")

    return SignupResponse(message="Signup received. Please verify your email.")


@router.post("/verify-email/{token}")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    result = await db.execute(select(PendingSignup))
    pending_signups = result.scalars().all()

    matched_signup: PendingSignup | None = None
    for signup in pending_signups:
        if verify_token(token, signup.verification_token_hash, signup.verification_token_salt):
            matched_signup = signup
            break

    if matched_signup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired verification token")

    # Token is valid — clear verification token so it can't be reused
    matched_signup.verification_token_hash = b""
    matched_signup.verification_token_salt = b""
    await db.commit()

    return {"message": "Email verified. Waiting for admin approval."}


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Always run one bcrypt verify (against a dummy hash when the user is missing)
    # so login timing doesn't reveal whether the account exists.
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(body.password, password_hash)
    if user is None or not password_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if user.status not in LOGIN_ALLOWED_STATUSES:
        detail = "Account is disabled" if user.status == UserStatus.disabled else "Account not yet approved by admin"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    access_token = create_access_token(str(user.id))
    return LoginResponse(access_token=access_token)


@router.post("/login-form")
@limiter.limit("10/minute")
async def login_form(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    form = await request.form()
    email = form.get("email")
    password = form.get("password")

    if not email or not password:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Email and password required")

    result = await db.execute(select(User).where(User.email == str(email)))
    user = result.scalar_one_or_none()

    # Always run one bcrypt verify (against a dummy hash when the user is missing)
    # so login timing doesn't reveal whether the account exists.
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(str(password), password_hash)
    if user is None or not password_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if user.status not in LOGIN_ALLOWED_STATUSES:
        detail = "Account is disabled" if user.status == UserStatus.disabled else "Account not yet approved by admin"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

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


@router.post("/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session", path="/")
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.put("/locale")
async def update_locale(
    body: LocaleUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    from app.i18n import get_supported_locales

    if body.locale not in get_supported_locales():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported locale")

    user.locale = body.locale
    db.add(user)
    await db.commit()
    return {"locale": body.locale}
