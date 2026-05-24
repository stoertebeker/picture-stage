from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.passwords import hash_password, hash_token, verify_password, verify_token
from app.auth.schemas import LoginRequest, LoginResponse, SignupRequest, SignupResponse, UserResponse
from app.auth.tokens import create_access_token, generate_verification_token
from app.db.models import PendingSignup, User
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)) -> SignupResponse:
    existing_user = await db.execute(select(User).where(User.email == body.email))
    if existing_user.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    existing_signup = await db.execute(select(PendingSignup).where(PendingSignup.email == body.email))
    if existing_signup.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Signup already pending")

    if len(body.password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must be at least 8 characters")

    verification_token = generate_verification_token()
    token_hash, token_salt = hash_token(verification_token)

    pending = PendingSignup(
        email=body.email,
        password_hash=hash_password(body.password),
        verification_token_hash=token_hash,
        verification_token_salt=token_salt,
    )
    db.add(pending)
    await db.commit()

    # TODO: send verification email via SMTP (issue ebm.7)
    return SignupResponse(
        message="Signup received. Please verify your email.",
        verification_token=verification_token,
    )


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
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if user.status == "pending":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account not yet approved by admin")

    access_token = create_access_token(str(user.id))
    return LoginResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
