"""Shared guest-facing service logic.

Single source of truth for the share-token resolver, the expiry gate and the
EXIF date parser used by both guest routers (the JSON API ``app/guest/router.py``
and the HTML viewer ``app/frontend/guest.py``) — previously duplicated in both
(picture-stage-d7z). Centralising the resolver in particular keeps the
owner-status (cxs) and expiry security checks in exactly one place, so a future
fix can no longer miss a copy.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, exists, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.passwords import verify_password, verify_token
from app.auth.token_lookup import legacy_token_scan, token_locator
from app.config import settings
from app.db.models import (
    LOGIN_ALLOWED_STATUSES,
    AuditLog,
    Gallery,
    GalleryStatus,
    Image,
    PreviewVariant,
    SelectionEvent,
    ShareSession,
    ShareSessionCleanupState,
    User,
)
from app.galleries.lifecycle import lock_gallery
from app.guest.credentials import browser_cookie_session_id
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.security.signing import sign_url
from app.selections.schemas import SelectionState
from app.selections.service import get_current_selections


async def resolve_gallery_by_token(token: str, db: AsyncSession) -> Gallery | None:
    """Resolve an indexed token, retaining a bounded scan for legacy links.

    Joins the owner and requires a login-allowed status so a disabled/pending
    photographer's share links stop resolving (cxs). The check lives in the
    query, so every guest endpoint inherits it via this single resolver, and an
    unlock restores access without touching share sessions.
    """
    query = (
        select(Gallery)
        .join(User, User.id == Gallery.owner_id)
        .where(
            Gallery.share_token_hash.isnot(None),
            Gallery.status.in_([GalleryStatus.shared, GalleryStatus.completed]),
            User.status.in_(LOGIN_ALLOWED_STATUSES),
        )
    )
    result = await db.execute(query.where(Gallery.share_token_lookup == token_locator(token)))
    for gallery in result.scalars():
        if gallery.share_token_hash and gallery.share_token_salt:
            if verify_token(token, gallery.share_token_hash, gallery.share_token_salt):
                return gallery

    # Older galleries can lack replayable plaintext, or have a stale locator
    # after an old app instance changes a link during a rolling upgrade. Their
    # salted verifier remains authoritative. Limit concurrent fallback scans and
    # fetch bounded batches; per-scan verifier work is still linear.
    async with legacy_token_scan("gallery"):
        legacy = await db.stream(query.execution_options(yield_per=100))
        try:
            async for gallery in legacy.scalars():
                if gallery.share_token_hash and gallery.share_token_salt:
                    if verify_token(token, gallery.share_token_hash, gallery.share_token_salt):
                        return gallery
        finally:
            await legacy.close()
    return None


async def lock_gallery_for_review(
    token: str, gallery_id: uuid.UUID, db: AsyncSession, *, shared: bool = False
) -> Gallery:
    """Lock one gallery, then recheck current access.

    Call after storage lifecycle fences and, for selections, the event-budget
    fence. Never lock the token resolver's legacy scan. A row lock also orders
    review writes against owner status UPDATEs, including completion/reopening.
    The separate post-lock query sees access changes committed while we waited.
    Shared mode admits readers together while excluding owner credential
    updates. Review mutations and session admission keep the exclusive default.
    Commit/rollback releases the lock; callers must not mutate before acquiring it.
    """
    with db.no_autoflush:
        locked_id = await db.scalar(select(Gallery.id).where(Gallery.id == gallery_id).with_for_update(read=shared))
        if locked_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
        gallery = await db.scalar(
            select(Gallery)
            .join(User, User.id == Gallery.owner_id)
            .where(
                Gallery.id == gallery_id,
                Gallery.status.in_([GalleryStatus.shared, GalleryStatus.completed]),
                User.status.in_(LOGIN_ALLOWED_STATUSES),
            )
            .execution_options(populate_existing=True)
        )
    if (
        gallery is None
        or gallery.share_token_hash is None
        or gallery.share_token_salt is None
        or not verify_token(token, gallery.share_token_hash, gallery.share_token_salt)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    check_gallery_accessible(gallery)
    return gallery


async def lock_gallery_for_guest_read(token: str, gallery_id: uuid.UUID, db: AsyncSession) -> Gallery:
    """Keep credential changes outside protected data rendering.

    Multiple readers may proceed together. Callers materialize
    their data then commit before returning; no upgrade to an exclusive lock.
    """
    await lock_gallery(db, gallery_id, shared=True)
    return await lock_gallery_for_review(token, gallery_id, db, shared=True)


async def create_share_session(
    token: str,
    gallery_id: uuid.UUID,
    request: Request,
    db: AsyncSession,
    *,
    password: str | None = None,
    reuse_browser_session: bool = False,
) -> tuple[Gallery, ShareSession]:
    """Validate current credentials and stamp their epoch in one transaction.

    Flush only: the caller commits after preparing the response's protected
    data, while the gallery lock still excludes credential changes and review
    mutations. All producers take the admission fence before any gallery row
    lock; taking FOR SHARE first would deadlock when cleanup needs FOR UPDATE.
    """
    gallery = await lock_gallery_for_session_admission(token, gallery_id, db)
    if gallery.password_hash is not None:
        if password is None or not verify_password(password, gallery.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    elif password is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    # Browser tabs share a cookie. Keep a valid visit and its CSRF context
    # stable, after password verification and the fresh gallery/epoch lock.
    # A completed visit must be replaced so an owner-reopened review can be
    # completed again; API callers retain their explicit new-visit contract.
    if reuse_browser_session:
        session_id = browser_cookie_session_id(request, gallery.id)
        existing_session = await current_share_session(gallery, session_id, db)
        if existing_session is not None and existing_session.completed_at is None:
            return gallery, existing_session

    await cleanup_expired_share_sessions(gallery.id, db)
    # LIMIT bounds legacy galleries already far above a newly lowered cap.
    retained = (
        select(ShareSession.id)
        .where(ShareSession.gallery_id == gallery.id)
        .limit(settings.max_share_sessions_per_gallery)
    )
    count = await db.scalar(select(func.count()).select_from(retained.subquery()))
    if (count or 0) >= settings.max_share_sessions_per_gallery:
        # These producers have only looked up/locked credentials before cleanup.
        # Persist safe reclamation and its checkpoint even when a lowered limit
        # needs several bounded passes before another visit fits. No grant is
        # created on this path, and historical rows remain untouched.
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Share session limit reached")

    session = ShareSession(
        gallery_id=gallery.id,
        authorization_epoch=gallery.authorization_epoch,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:512],
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.share_session_ttl_seconds),
    )
    db.add(session)
    await db.flush()
    return gallery, session


async def lock_gallery_for_session_admission(token: str, gallery_id: uuid.UUID, db: AsyncSession) -> Gallery:
    """Order lifecycle -> per-gallery admission -> exclusive gallery row.

    PostgreSQL transaction advisory locks serialize processes, independent of
    cookies, client IPs, and uvicorn worker count. The exclusive row lock also
    excludes selection/completion writers while retention checks run.
    """
    await lock_gallery(db, gallery_id, shared=True)
    digest = hashlib.sha256(b"picture-stage:share-session-budget:v1\0" + gallery_id.bytes).digest()
    with db.no_autoflush:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], "big", signed=True)}
        )
    return await lock_gallery_for_review(token, gallery_id, db)


SHARE_SESSION_CLEANUP_BATCH_SIZE = 100


async def cleanup_expired_share_sessions(gallery_id: uuid.UUID, db: AsyncSession) -> None:
    """Reclaim at most one small batch while holding admission/gallery locks.

    A durable cursor rotates past protected history and wraps at the end. Read
    at most one indexed window (plus one empty tail lookup when wrapping), then
    lock only those IDs before checking references. This also bounds SKIP LOCKED
    work when many sessions have in-flight FK writers. Never cascade selection
    history or orphan an audit attribution. NULL legacy grants are not visited.
    """
    now = datetime.now(UTC)
    with db.no_autoflush:
        checkpoint = await db.get(ShareSessionCleanupState, gallery_id)
        if checkpoint is None:
            checkpoint = ShareSessionCleanupState(gallery_id=gallery_id)
            db.add(checkpoint)
        window = (
            select(ShareSession.id, ShareSession.expires_at)
            .where(
                ShareSession.gallery_id == gallery_id,
                ShareSession.expires_at <= now,
                ShareSession.completed_at.is_(None),
            )
            .order_by(ShareSession.expires_at, ShareSession.id)
            .limit(SHARE_SESSION_CLEANUP_BATCH_SIZE)
        )
        has_checkpoint = checkpoint.last_expires_at is not None and checkpoint.last_session_id is not None
        after_checkpoint = window
        if has_checkpoint:
            after_checkpoint = window.where(
                tuple_(ShareSession.expires_at, ShareSession.id)
                > (checkpoint.last_expires_at, checkpoint.last_session_id)
            )
        rows = (await db.execute(after_checkpoint)).all()
        if not rows and has_checkpoint:
            rows = (await db.execute(window)).all()
        if not rows:
            return
        checkpoint.last_session_id, checkpoint.last_expires_at = rows[-1]
        candidates = list(
            (
                await db.scalars(
                    select(ShareSession.id)
                    .where(ShareSession.id.in_([row.id for row in rows]))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        if candidates:
            await db.execute(
                delete(ShareSession)
                .where(
                    ShareSession.id.in_(candidates),
                    ShareSession.expires_at <= now,
                    ShareSession.completed_at.is_(None),
                    ~exists(select(SelectionEvent.id).where(SelectionEvent.share_session_id == ShareSession.id)),
                    ~exists(select(AuditLog.id).where(AuditLog.actor_session_id == ShareSession.id)),
                )
                .execution_options(synchronize_session=False)
            )


async def current_share_session(
    gallery: Gallery, session_id: uuid.UUID | None, db: AsyncSession
) -> ShareSession | None:
    """Preserve old sessions for attribution but reject their obsolete grants."""
    if session_id is None:
        return None
    session: ShareSession | None = await db.scalar(
        select(ShareSession)
        .where(
            ShareSession.id == session_id,
            ShareSession.gallery_id == gallery.id,
            ShareSession.authorization_epoch == gallery.authorization_epoch,
            or_(ShareSession.expires_at.is_(None), ShareSession.expires_at > datetime.now(UTC)),
        )
        .execution_options(populate_existing=True)
    )
    return session


def is_gallery_expired(gallery: Gallery) -> bool:
    """Return True if the gallery's share link has passed its expiry instant."""
    expiry = gallery.expires_at
    if expiry is None:
        return False
    # asyncpg decodes existing PostgreSQL +/-infinity as naive datetime.max/min.
    # Treat these (and legacy naive values) as UTC without mutating the record.
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry < datetime.now(UTC)


def check_gallery_accessible(gallery: Gallery) -> None:
    """Raise 410 Gone if the gallery's share link has expired."""
    if is_gallery_expired(gallery):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Gallery link has expired")


async def require_gallery_read_access(
    gallery: Gallery,
    session_id: uuid.UUID | None,
    db: AsyncSession,
    *,
    token: str,
) -> Gallery:
    """Require an unlocked gallery session before returning password-protected data.

    The share token identifies the gallery, but a configured password is a second
    access-control boundary. Passwordless galleries intentionally remain usable
    with the token alone; protected galleries require a session created by the
    successful password-verification flow.
    """
    gallery = await lock_gallery_for_guest_read(token, gallery.id, db)
    if gallery.password_hash is None:
        return gallery

    if session_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active session")

    if await current_share_session(gallery, session_id, db) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active session")
    return gallery


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
