"""Shared owner gallery commands; callers own commit and response rendering."""

import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_token
from app.auth.token_lookup import token_locator
from app.db.models import ALLOWED_TRANSITIONS, Gallery, GalleryStatus, User
from app.galleries.authorization import lock_owned_gallery_authorization


async def get_owned_gallery(gallery_id: uuid.UUID | str, user: User, db: AsyncSession) -> Gallery:
    """Load a gallery without revealing whether another owner's gallery exists."""
    result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return gallery


async def create_gallery_share(
    gallery_id: uuid.UUID, owner_id: uuid.UUID, db: AsyncSession, *, password_hash: str | None
) -> tuple[Gallery, str]:
    """Rotate credentials after the caller has finished costly password hashing."""
    token = secrets.token_urlsafe(32)
    token_hash, token_salt = hash_token(token)

    gallery = await lock_owned_gallery_authorization(gallery_id, owner_id, db)
    gallery.authorization_epoch += 1
    gallery.share_token_hash = token_hash
    gallery.share_token_salt = token_salt
    gallery.share_token = token
    gallery.share_token_lookup = token_locator(token)
    gallery.password_hash = password_hash
    if gallery.status == GalleryStatus.draft:
        gallery.status = GalleryStatus.shared
    return gallery, token


async def revoke_gallery_share(gallery_id: uuid.UUID, owner_id: uuid.UUID, db: AsyncSession) -> Gallery:
    """Clear credentials and invalidate grants while retaining session history."""
    gallery = await lock_owned_gallery_authorization(gallery_id, owner_id, db)
    gallery.authorization_epoch += 1
    gallery.share_token_hash = None
    gallery.share_token_salt = None
    gallery.share_token = None
    gallery.share_token_lookup = None
    gallery.password_hash = None
    if gallery.status == GalleryStatus.shared:
        gallery.status = GalleryStatus.draft
    return gallery


async def update_gallery_password(
    gallery_id: uuid.UUID, owner_id: uuid.UUID, db: AsyncSession, *, password_hash: str | None
) -> Gallery:
    """Update a precomputed password hash without rotating the share link."""
    gallery = await lock_owned_gallery_authorization(gallery_id, owner_id, db)
    gallery.password_hash = password_hash
    gallery.authorization_epoch += 1
    return gallery


def change_gallery_status(gallery: Gallery, target: GalleryStatus) -> bool:
    """Validate and apply a transition, returning whether it needs a commit."""
    if target == gallery.status:
        return False
    if target not in ALLOWED_TRANSITIONS.get(gallery.status, set()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Transition from '{gallery.status.value}' to '{target.value}' is not allowed",
        )
    if target == GalleryStatus.shared and gallery.share_token_hash is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot share gallery without a share link. Create one first.",
        )
    gallery.status = target
    return True
