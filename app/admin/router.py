from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.auth.schemas import (
    AdminUserListResponse,
    AdminUserResponse,
    PendingSignupCountResponse,
    PendingSignupResponse,
    UserResponse,
)
from app.db.models import Gallery, PendingSignup, User, UserStatus
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


@router.get("/pending-signups/count", response_model=PendingSignupCountResponse)
async def pending_signups_count(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PendingSignupCountResponse:
    """Lightweight count of open signup requests, used for the admin nav badge."""
    count = await db.scalar(select(func.count()).select_from(PendingSignup))
    return PendingSignupCountResponse(count=count or 0)


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    status_filter: UserStatus | None = Query(None, alias="status"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    """Paginated list of real user accounts (active/admin/disabled) with gallery counts.

    Pending signups live in their own table and are listed via /pending-signups.
    """
    count_stmt = select(func.count()).select_from(User)
    if status_filter is not None:
        count_stmt = count_stmt.where(User.status == status_filter)
    total = await db.scalar(count_stmt) or 0

    stmt = (
        select(User, func.count(Gallery.id))
        .outerjoin(Gallery, Gallery.owner_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    if status_filter is not None:
        stmt = stmt.where(User.status == status_filter)

    rows = (await db.execute(stmt)).all()
    users = [
        AdminUserResponse(
            id=user.id,
            email=user.email,
            status=str(user.status),
            locale=user.locale,
            email_verified_at=user.email_verified_at,
            created_at=user.created_at,
            galleries_count=galleries_count,
        )
        for user, galleries_count in rows
    ]
    return AdminUserListResponse(users=users, total=total, page=page, per_page=per_page)
