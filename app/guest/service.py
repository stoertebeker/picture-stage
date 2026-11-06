"""Shared guest-facing service logic.

Single source of truth for the share-token resolver, the expiry gate and the
EXIF date parser used by both guest routers (the JSON API ``app/guest/router.py``
and the HTML viewer ``app/frontend/guest.py``) — previously duplicated in both
(picture-stage-d7z). Centralising the resolver in particular keeps the
owner-status (cxs) and expiry security checks in exactly one place, so a future
fix can no longer miss a copy.
"""

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, exists, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.lookup_progress import LOOKUP_WINDOW, LookupProgress
from app.auth.passwords import verify_password, verify_token
from app.auth.token_lookup import legacy_token_scan, token_locator
from app.config import settings
from app.db.models import (
    LOGIN_ALLOWED_STATUSES,
    AuditLog,
    CurrentSelection,
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
from app.images.capture_date import parse_exif_date  # noqa: F401 -- shared parser compatibility
from app.security.signing import sign_url
from app.selections.schemas import SelectionState


async def resolve_gallery_by_token(token: str, db: AsyncSession) -> Gallery | None:
    """Resolve fixed indexed/legacy windows, continuing across HTTP requests.

    Select IDs before access filters: a large inactive population must not make
    even an invalid token scan unbounded rows. Migration 0017 and its write
    trigger keep unrecoverable/stale locators in the partial legacy ID index.
    Hints never authorize access and no lookup writes or row locks are taken.
    """
    from urllib.parse import quote

    progress = LookupProgress("gallery", token, f"/g/{quote(token, safe='')}")
    cursor = progress.read()
    query = (
        select(Gallery)
        .join(User, User.id == Gallery.owner_id)
        .where(
            Gallery.share_token_hash.isnot(None),
            Gallery.status.in_([GalleryStatus.shared, GalleryStatus.completed]),
            User.status.in_(LOGIN_ALLOWED_STATUSES),
        )
        .execution_options(populate_existing=True)
    )
    if cursor is not None and cursor[0] == "h":
        hinted = await db.scalar(query.where(Gallery.id == uuid.UUID(hex=cursor[1])))
        if hinted is not None and hinted.share_token_hash and hinted.share_token_salt:
            if verify_token(token, hinted.share_token_hash, hinted.share_token_salt):
                return hinted
        cursor = None

    async def check_window(ids: list[uuid.UUID]) -> Gallery | None:
        if not ids:
            return None
        candidates = (await db.scalars(query.where(Gallery.id.in_(ids)))).all()
        for gallery in candidates:
            if gallery.share_token_hash and gallery.share_token_salt:
                if verify_token(token, gallery.share_token_hash, gallery.share_token_salt):
                    progress.save("h", gallery.id.hex)
                    return gallery
        return None

    if cursor is None or cursor[0] == "i":
        indexed = select(Gallery.id).where(Gallery.share_token_lookup == token_locator(token))
        if cursor is not None:
            indexed = indexed.where(Gallery.id > uuid.UUID(hex=cursor[1]))
        ids = list((await db.scalars(indexed.order_by(Gallery.id).limit(LOOKUP_WINDOW + 1))).all())
        found = await check_window(ids[:LOOKUP_WINDOW])
        if found is not None:
            return found
        if len(ids) > LOOKUP_WINDOW:
            progress.retry("i", ids[LOOKUP_WINDOW - 1].hex)
        cursor = None

    # The process admission cap is additional protection; each admitted request
    # itself performs only a fixed window, with no open stream across retries.
    async with legacy_token_scan("gallery"):
        legacy = select(Gallery.id).where(Gallery.share_token_lookup.is_(None), Gallery.share_token_hash.isnot(None))
        if cursor is not None:
            legacy = legacy.where(Gallery.id > uuid.UUID(hex=cursor[1]))
        ids = list((await db.scalars(legacy.order_by(Gallery.id).limit(LOOKUP_WINDOW + 1))).all())
        found = await check_window(ids[:LOOKUP_WINDOW])
        if found is not None:
            return found
        if len(ids) > LOOKUP_WINDOW:
            progress.retry("l", ids[LOOKUP_WINDOW - 1].hex)
    progress.clear()
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


GALLERY_PAGE_SIZE = 30
# A 512-character filename can require six JSON bytes per control character.
# With fixed metadata, base64 and HMAC the largest emitted cursor is 4320
# characters. Keep a finite shared request/parser cap above that boundary.
MAX_CURSOR_LENGTH = 8192


@dataclass
class GuestImagePage:
    images: list[Image]
    selections: dict[uuid.UUID, SelectionState]
    next_cursor: str | None
    total_images: int
    selected_count: int
    favorited_count: int
    filtered_count: int


def _cursor_signature(payload: str) -> str:
    return hmac.new(settings.hmac_secret_key.encode(), b"guest-page:v1:" + payload.encode(), hashlib.sha256).hexdigest()


def _encode_page_cursor(
    gallery: Gallery, sort_by: ImageSortBy, sort_dir: SortDirection, filter_mode: ImageFilter, keys: list[Any]
) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                [1, str(gallery.id), sort_by.value, sort_dir.value, filter_mode.value, GALLERY_PAGE_SIZE, keys],
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return payload + "." + _cursor_signature(payload)


def _decode_page_cursor(
    cursor: str, gallery: Gallery, sort_by: ImageSortBy, sort_dir: SortDirection, filter_mode: ImageFilter
) -> list[Any]:
    try:
        if len(cursor) > MAX_CURSOR_LENGTH:
            raise ValueError
        payload, signature = cursor.split(".")
        if not hmac.compare_digest(signature, _cursor_signature(payload)):
            raise ValueError
        data = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True))
        if (
            not isinstance(data, list)
            or len(data) != 7
            or type(data[0]) is not int
            or type(data[5]) is not int
            or data[:6] != [1, str(gallery.id), sort_by.value, sort_dir.value, filter_mode.value, GALLERY_PAGE_SIZE]
        ):
            raise ValueError
        keys = data[6]
        expected = 3 if sort_by == ImageSortBy.exif_date else 2
        if not isinstance(keys, list) or len(keys) != expected or not isinstance(keys[-1], str):
            raise ValueError
        keys[-1] = uuid.UUID(keys[-1])
        if sort_by == ImageSortBy.exif_date:
            if not isinstance(keys[0], str) or type(keys[1]) is not int:
                raise ValueError
            keys[0] = datetime.strptime(keys[0], "%Y-%m-%dT%H:%M:%S")
        elif sort_by == ImageSortBy.filename:
            if not isinstance(keys[0], str) or len(keys[0]) > 512:
                raise ValueError
        elif type(keys[0]) is not int:
            raise ValueError
        if sort_by != ImageSortBy.filename and not -(2**31) <= keys[-2] < 2**31:
            raise ValueError
        return keys
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail="Invalid image page cursor") from exc


def _selection_filter(filter_mode: ImageFilter) -> Any:
    selected = func.coalesce(CurrentSelection.selected, False)
    favorited = func.coalesce(CurrentSelection.favorited, False)
    if filter_mode == ImageFilter.selected:
        return selected.is_(True)
    if filter_mode == ImageFilter.favorited:
        return favorited.is_(True)
    if filter_mode == ImageFilter.unrated:
        return selected.is_(False) & favorited.is_(False)
    return True


async def load_guest_image_page(
    gallery: Gallery,
    db: AsyncSession,
    sort_by: ImageSortBy = ImageSortBy.sort_order,
    sort_dir: SortDirection = SortDirection.asc,
    filter_mode: ImageFilter = ImageFilter.all,
    cursor: str | None = None,
) -> GuestImagePage:
    """Bound keys in SQL before hydrating images, previews or review state.

    All guest image surfaces share this boundary. Counts aggregate in SQL and
    no request reads event history. Keyset cursors cannot request large offsets.
    The caller holds the existing guest credential read fence through rendering.
    """
    columns = [Image.sort_order, Image.id]
    if sort_by == ImageSortBy.filename:
        columns = [Image.filename, Image.id]
    elif sort_by == ImageSortBy.exif_date:
        columns = [Image.capture_sort_key, Image.sort_order, Image.id]
    predicate = _selection_filter(filter_mode)
    keys_query = (
        select(*columns)
        .outerjoin(CurrentSelection, CurrentSelection.image_id == Image.id)
        .where(Image.gallery_id == gallery.id, predicate)
    )
    if cursor is not None:
        keys = _decode_page_cursor(cursor, gallery, sort_by, sort_dir, filter_mode)
        comparison = (
            tuple_(*columns) < tuple_(*keys) if sort_dir == SortDirection.desc else tuple_(*columns) > tuple_(*keys)
        )
        keys_query = keys_query.where(comparison)
    order = [col.desc() if sort_dir == SortDirection.desc else col.asc() for col in columns]
    rows = list((await db.execute(keys_query.order_by(*order).limit(GALLERY_PAGE_SIZE + 1))).all())
    has_more = len(rows) > GALLERY_PAGE_SIZE
    rows = rows[:GALLERY_PAGE_SIZE]
    ids = [row[-1] for row in rows]
    images = (
        list(
            (
                await db.scalars(
                    select(Image)
                    .where(Image.gallery_id == gallery.id, Image.id.in_(ids))
                    .options(selectinload(Image.previews))
                    .order_by(*order)
                )
            ).all()
        )
        if ids
        else []
    )
    states = (await db.scalars(select(CurrentSelection).where(CurrentSelection.image_id.in_(ids)))).all() if ids else []
    selections = {
        state.image_id: SelectionState(
            image_id=state.image_id, selected=state.selected, favorited=state.favorited, comment=state.comment
        )
        for state in states
    }
    counts = (
        await db.execute(
            select(
                func.count(Image.id),
                func.count().filter(CurrentSelection.selected.is_(True)),
                func.count().filter(CurrentSelection.favorited.is_(True)),
                func.count().filter(predicate),
            )
            .select_from(Image)
            .outerjoin(CurrentSelection, CurrentSelection.image_id == Image.id)
            .where(Image.gallery_id == gallery.id)
        )
    ).one()
    next_cursor = None
    if has_more:
        last_keys = [
            value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, uuid.UUID) else value
            for value in rows[-1]
        ]
        next_cursor = _encode_page_cursor(gallery, sort_by, sort_dir, filter_mode, last_keys)
    return GuestImagePage(images, selections, next_cursor, *counts)


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
