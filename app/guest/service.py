"""Shared guest-facing service logic.

Single source of truth for the share-token resolver, the expiry gate and the
EXIF date parser used by both guest routers (the JSON API ``app/guest/router.py``
and the HTML viewer ``app/frontend/guest.py``) — previously duplicated in both
(picture-stage-d7z). Centralising the resolver in particular keeps the
owner-status (cxs) and expiry security checks in exactly one place, so a future
fix can no longer miss a copy.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.passwords import verify_token
from app.db.models import (
    LOGIN_ALLOWED_STATUSES,
    Gallery,
    GalleryStatus,
    Image,
    PreviewVariant,
    User,
)
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.security.signing import sign_url
from app.selections.schemas import SelectionState
from app.selections.service import get_current_selections


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


async def load_sorted_filtered_images(
    gallery: Gallery,
    db: AsyncSession,
    sort_by: ImageSortBy = ImageSortBy.sort_order,
    sort_dir: SortDirection = SortDirection.asc,
    filter_mode: ImageFilter = ImageFilter.all,
) -> tuple[list[Image], dict[uuid.UUID, SelectionState]]:
    """Load a gallery's images, sorted and filtered, plus the selection map.

    Returns the (sorted, filtered) Image rows and a gallery-wide selection map
    (image_id -> SelectionState) so each caller can serialise to its own shape
    (the JSON API drops the selection fields; the HTML viewer keeps them). The
    selections are always loaded — even for filter=all — so the map is available
    to both callers from a single query.
    """
    order_col: Any = Image.sort_order
    if sort_by == ImageSortBy.filename:
        order_col = Image.filename
    order_clause = order_col.desc() if sort_dir == SortDirection.desc else order_col.asc()

    result = await db.execute(
        select(Image).where(Image.gallery_id == gallery.id).options(selectinload(Image.previews)).order_by(order_clause)
    )
    images = list(result.scalars().all())

    if sort_by == ImageSortBy.exif_date:
        far_future = datetime(9999, 1, 1)
        images.sort(
            key=lambda img: parse_exif_date(img.exif) or far_future,
            reverse=(sort_dir == SortDirection.desc),
        )

    selections = await get_current_selections(gallery.id, db)
    sel_map: dict[uuid.UUID, SelectionState] = {s.image_id: s for s in selections}

    if filter_mode == ImageFilter.selected:
        images = [img for img in images if (s := sel_map.get(img.id)) and s.selected]
    elif filter_mode == ImageFilter.favorited:
        images = [img for img in images if (s := sel_map.get(img.id)) and s.favorited]
    elif filter_mode == ImageFilter.unrated:
        images = [img for img in images if not (s := sel_map.get(img.id)) or (not s.selected and not s.favorited)]

    return images, sel_map


def sign_preview_urls(img: Image) -> dict[str, str]:
    """Build signed thumbnail/preview URLs for an image's preview variants.

    Thumbnails get a 1h TTL, the larger preview a 15min TTL (matching the
    project's signed-URL policy). Missing variants are simply absent from the map.
    """
    preview_urls: dict[str, str] = {}
    for preview in img.previews:
        if preview.variant == PreviewVariant.thumb_sm:
            preview_urls["thumb_sm"] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)
        elif preview.variant == PreviewVariant.thumb_md:
            preview_urls["thumb_md"] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)
        elif preview.variant == PreviewVariant.preview:
            preview_urls["preview"] = sign_url(f"/media/{preview.storage_key}", expires_in=900)
    return preview_urls
