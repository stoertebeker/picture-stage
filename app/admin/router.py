import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.auth.passwords import hash_password
from app.auth.schemas import (
    AdminPasswordResetRequest,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserStatusUpdate,
    PendingSignupCountResponse,
    PendingSignupResponse,
    UserResponse,
)
from app.db.models import AuditLog, Gallery, PendingSignup, User, UserStatus
from app.db.session import get_db
from app.galleries.deletion import purge_gallery
from app.security.rate_limit import limiter
from app.storage.base import StorageBackend
from app.storage.dependencies import get_storage

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


async def _count_admins(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.admin)) or 0


async def _admin_user_response(user: User, db: AsyncSession) -> AdminUserResponse:
    galleries_count = await db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == user.id)) or 0
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        status=str(user.status),
        locale=user.locale,
        email_verified_at=user.email_verified_at,
        created_at=user.created_at,
        galleries_count=galleries_count,
    )


@router.patch("/users/{user_id}/status", response_model=AdminUserResponse)
@limiter.limit("30/minute")
async def update_user_status(
    request: Request,
    user_id: uuid.UUID,
    body: AdminUserStatusUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Promote/demote (active <-> admin) or lock/unlock (disabled) a user."""
    new_status = body.status
    if new_status == UserStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot set a user to 'pending'")

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # S1: an admin must not change their own status (avoids self-lockout).
    if target.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot change your own status")

    old_status = target.status
    # S2: never demote/lock the last remaining admin (avoids system lockout).
    if old_status == UserStatus.admin and new_status != UserStatus.admin and await _count_admins(db) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot demote the last remaining admin")

    if new_status != old_status:
        target.status = new_status
        db.add(
            AuditLog(
                gallery_id=None,
                event_type="user_status_changed",
                actor_user_id=admin.id,
                details={
                    "target_user_id": str(target.id),
                    "target_email": target.email,
                    "old_status": str(old_status),
                    "new_status": str(new_status),
                },
            )
        )
        await db.commit()
        await db.refresh(target)

    return await _admin_user_response(target, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_user(
    request: Request,
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> None:
    """Permanently delete a user, including all their galleries and storage files."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # S1: no self-deletion.
    if target.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot delete your own account")

    # S2: never delete the last remaining admin.
    if target.status == UserStatus.admin and await _count_admins(db) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete the last remaining admin")

    # Audit BEFORE deletion (gallery_id=None: this is a user-level, not gallery, event).
    db.add(
        AuditLog(
            gallery_id=None,
            event_type="user_deleted",
            actor_user_id=admin.id,
            details={"target_user_id": str(target.id), "target_email": target.email, "status": str(target.status)},
        )
    )
    await db.flush()

    # Storage-aware: purge each gallery (files + DB rows) so no image data is orphaned.
    galleries = (await db.execute(select(Gallery).where(Gallery.owner_id == target.id))).scalars().all()
    for gallery in galleries:
        await purge_gallery(gallery, db, storage)

    # Core delete avoids async lazy-load of cascade relationships; remaining dependents
    # (notification_configs -> deliveries) are removed by DB-level ON DELETE CASCADE.
    await db.execute(delete(User).where(User.id == target.id))
    await db.commit()


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def reset_user_password(
    request: Request,
    user_id: uuid.UUID,
    body: AdminPasswordResetRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Set a new password for a user (admin-initiated reset).

    Note: auth is stateless JWT, so existing sessions remain valid until their
    token expires. The reset is audit-logged. The new password is never logged.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.password_hash = hash_password(body.new_password)
    db.add(
        AuditLog(
            gallery_id=None,
            event_type="user_password_reset",
            actor_user_id=admin.id,
            details={"target_user_id": str(target.id), "target_email": target.email},
        )
    )
    await db.commit()
