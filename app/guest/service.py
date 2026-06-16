"""Shared guest-facing service logic.

Single source of truth for the share-token resolver, the expiry gate and the
EXIF date parser used by both guest routers (the JSON API ``app/guest/router.py``
and the HTML viewer ``app/frontend/guest.py``) — previously duplicated in both
(picture-stage-d7z). Centralising the resolver in particular keeps the
owner-status (cxs) and expiry security checks in exactly one place, so a future
fix can no longer miss a copy.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import verify_token
from app.db.models import (
    LOGIN_ALLOWED_STATUSES,
    Gallery,
    GalleryStatus,
    User,
)


async def resolve_gallery_by_token(token: str, db: AsyncSession) -> Gallery | None:
    """Resolve a share token to a Gallery, checking the hash against all shared galleries.

    Joins the owner and requires a login-allowed status so a disabled/pending
    photographer's share links stop resolving (cxs). The check lives in the
    query, so every guest endpoint inherits it via this single resolver, and an
    unlock restores access without touching share sessions.
    """
    result = await db.execute(
        select(Gallery)
        .join(User, User.id == Gallery.owner_id)
        .where(
            Gallery.share_token_hash.isnot(None),
            Gallery.status.in_([GalleryStatus.shared, GalleryStatus.completed]),
            User.status.in_(LOGIN_ALLOWED_STATUSES),
        )
    )
    galleries = result.scalars().all()

    for gallery in galleries:
        if gallery.share_token_hash and gallery.share_token_salt:
            if verify_token(token, gallery.share_token_hash, gallery.share_token_salt):
                return gallery
    return None


def is_gallery_expired(gallery: Gallery) -> bool:
    """Return True if the gallery's share link has passed its expiry instant."""
    return bool(gallery.expires_at and gallery.expires_at < datetime.now(UTC))


def check_gallery_accessible(gallery: Gallery) -> None:
    """Raise 410 Gone if the gallery's share link has expired."""
    if is_gallery_expired(gallery):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Gallery link has expired")


def parse_exif_date(exif: dict[str, Any] | None) -> datetime | None:
    """Parse the EXIF capture date from image metadata, or None if absent/malformed."""
    if not exif:
        return None
    raw = exif.get("DateTimeOriginal") or exif.get("DateTime")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
