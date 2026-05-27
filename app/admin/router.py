from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.auth.schemas import PendingSignupResponse, UserResponse
from app.db.models import PendingSignup, User, UserStatus
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/pending-signups", response_model=list[PendingSignupResponse])
async def list_pending_signups(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PendingSignup]:
    result = await db.execute(select(PendingSignup).order_by(PendingSignup.requested_at.desc()))
    return list(result.scalars().all())


@router.post("/approve/{signup_id}", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def approve_signup(
    signup_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    result = await db.execute(select(PendingSignup).where(PendingSignup.id == signup_id))
    signup = result.scalar_one_or_none()

    if signup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending signup not found")

    existing = await db.execute(select(User).where(User.email == signup.email))
    if existing.scalar_one_or_none() is not None:
        await db.delete(signup)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User with this email already exists")

    user = User(
        email=signup.email,
        password_hash=signup.password_hash,
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.delete(signup)
    await db.commit()
    await db.refresh(user)

    return user


@router.delete("/reject/{signup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def reject_signup(
    signup_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(PendingSignup).where(PendingSignup.id == signup_id))
    signup = result.scalar_one_or_none()

    if signup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending signup not found")

    await db.delete(signup)
    await db.commit()
