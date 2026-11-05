import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service
from app.admin.service import AdminActionError
from app.auth.dependencies import require_admin
from app.auth.schemas import (
    AdminPasswordResetRequest,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserStatusUpdate,
    PendingSignupCountResponse,
    PendingSignupResponse,
    UserResponse,
)
from app.db.models import PendingSignup, User, UserStatus
from app.db.session import get_db
from app.security.rate_limit import limiter
from app.storage.base import StorageBackend
from app.storage.dependencies import get_storage

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def _user_response(user: User, db: AsyncSession) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        status=str(user.status),
        locale=user.locale,
        email_verified_at=user.email_verified_at,
        created_at=user.created_at,
        galleries_count=await service.galleries_count(db, user.id),
    )


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
    try:
        return await service.approve_pending_signup(db, signup_id)
    except AdminActionError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err


@router.delete("/reject/{signup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def reject_signup(
    signup_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await service.reject_pending_signup(db, signup_id)
    except AdminActionError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err


@router.get("/pending-signups/count", response_model=PendingSignupCountResponse)
async def pending_signups_count(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PendingSignupCountResponse:
    """Lightweight count of open signup requests, used for the admin nav badge."""
    return PendingSignupCountResponse(count=await service.count_pending_signups(db))


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
    rows, total = await service.list_users(db, page=page, per_page=per_page, status_filter=status_filter)
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
    try:
        target = await service.change_user_status(db, actor=admin, target_id=user_id, new_status=body.status)
    except AdminActionError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return await _user_response(target, db)


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
    try:
        await service.delete_user(db, storage, actor=admin, target_id=user_id)
    except AdminActionError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err


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

    Existing access tokens are invalidated immediately: the reset bumps the
    user's tokens_valid_after cut-off, so any session holding the old token is
    rejected on its next request. The reset is audit-logged; the new password is
    never logged.
    """
    try:
        await service.reset_user_password(db, actor=admin, target_id=user_id, new_password=body.new_password)
    except AdminActionError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
