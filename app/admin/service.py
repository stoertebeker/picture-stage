"""Admin user-management service layer.

Holds the business logic + security guardrails for managing user accounts so
that BOTH the JSON API (app/admin/router.py) and the cookie-authenticated
frontend (app/frontend/admin.py) share one implementation. This guarantees the
guardrails (no self-sabotage, last-admin protection, audit logging) can never
drift apart between the two entry points.

Functions raise AdminActionError on rejected actions; each caller translates it
into its own response (HTTPException for the API, a toast for the frontend).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.db.models import AuditLog, Gallery, PendingSignup, User, UserStatus
from app.galleries.deletion import purge_gallery
from app.storage.base import StorageBackend


class AdminActionError(Exception):
    """A rejected admin action. ``i18n_key`` lets the frontend localise it."""

    def __init__(self, status_code: int, detail: str, i18n_key: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.i18n_key = i18n_key


async def count_admins(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.admin)) or 0


async def count_pending_signups(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(PendingSignup)) or 0


async def galleries_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return await db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == user_id)) or 0


async def list_users(
    db: AsyncSession,
    *,
    page: int,
    per_page: int,
    status_filter: UserStatus | None,
) -> tuple[list[tuple[User, int]], int]:
    """Return ((user, galleries_count) rows for the page, total count)."""
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

    rows = [(user, gc) for user, gc in (await db.execute(stmt)).all()]
    return rows, total


async def _get_target(db: AsyncSession, target_id: uuid.UUID) -> User:
    target = await db.get(User, target_id)
    if target is None:
        raise AdminActionError(404, "User not found", "admin.err_user_not_found")
    return target


async def change_user_status(
    db: AsyncSession,
    *,
    actor: User,
    target_id: uuid.UUID,
    new_status: UserStatus,
) -> User:
    """Promote/demote (active<->admin) or lock/unlock (disabled) a user."""
    if new_status == UserStatus.pending:
        raise AdminActionError(400, "Cannot set a user to 'pending'", "admin.err_status_pending")

    target = await _get_target(db, target_id)

    # S1: an admin must not change their own status (avoids self-lockout).
    if target.id == actor.id:
        raise AdminActionError(400, "You cannot change your own status", "admin.err_self_status")

    old_status = target.status
    # S2: never demote/lock the last remaining admin (defence in depth; shadowed by S1).
    if old_status == UserStatus.admin and new_status != UserStatus.admin and await count_admins(db) <= 1:
        raise AdminActionError(409, "Cannot demote the last remaining admin", "admin.err_last_admin")

    if new_status != old_status:
        target.status = new_status
        # Locking a user must also kill already-issued tokens. The status gate in
        # require_active_user already blocks them, but this is defence in depth for
        # any route that depends only on get_current_user without a status check.
        if new_status == UserStatus.disabled:
            target.tokens_valid_after = datetime.now(UTC)
        db.add(
            AuditLog(
                gallery_id=None,
                event_type="user_status_changed",
                actor_user_id=actor.id,
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

    return target


async def delete_user(
    db: AsyncSession,
    storage: StorageBackend,
    *,
    actor: User,
    target_id: uuid.UUID,
) -> None:
    """Permanently delete a user, including all their galleries and storage files."""
    target = await _get_target(db, target_id)

    # S1: no self-deletion.
    if target.id == actor.id:
        raise AdminActionError(400, "You cannot delete your own account", "admin.err_self_delete")

    # S2: never delete the last remaining admin.
    if target.status == UserStatus.admin and await count_admins(db) <= 1:
        raise AdminActionError(409, "Cannot delete the last remaining admin", "admin.err_last_admin")

    # Audit BEFORE deletion (gallery_id=None: user-level, not gallery, event).
    db.add(
        AuditLog(
            gallery_id=None,
            event_type="user_deleted",
            actor_user_id=actor.id,
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


async def reset_user_password(
    db: AsyncSession,
    *,
    actor: User,
    target_id: uuid.UUID,
    new_password: str,
) -> User:
    """Set a new password for a user (admin-initiated). The password is never logged."""
    target = await _get_target(db, target_id)
    target.password_hash = hash_password(new_password)
    # Invalidate any access token issued before now: a reset must log out
    # sessions that still hold the old credentials (the whole point of a reset).
    target.tokens_valid_after = datetime.now(UTC)
    db.add(
        AuditLog(
            gallery_id=None,
            event_type="user_password_reset",
            actor_user_id=actor.id,
            details={"target_user_id": str(target.id), "target_email": target.email},
        )
    )
    await db.commit()
    await db.refresh(target)
    return target


async def set_gallery_limit_override(
    db: AsyncSession,
    *,
    actor: User,
    target_id: uuid.UUID,
    override: int | None,
) -> User:
    """Set or clear a per-user gallery-limit override.

    override=None clears the override (user falls back to global default).
    override=0    means unlimited for this user.
    override>0    sets a specific cap.
    Negative values are rejected.
    """
    if override is not None and override < 0:
        raise AdminActionError(
            400, "Gallery limit must be 0 (unlimited) or a positive number", "admin.err_invalid_limit"
        )

    target = await _get_target(db, target_id)
    old_override = target.gallery_limit_override
    target.gallery_limit_override = override
    db.add(
        AuditLog(
            gallery_id=None,
            event_type="user_gallery_limit_changed",
            actor_user_id=actor.id,
            details={
                "target_user_id": str(target.id),
                "target_email": target.email,
                "old_override": old_override,
                "new_override": override,
            },
        )
    )
    await db.commit()
    await db.refresh(target)
    return target
