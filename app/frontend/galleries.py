"""Frontend gallery management: detail, upload, share, status transitions, expiry."""

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_authenticated_page
from app.auth.passwords import hash_password, hash_token
from app.db.models import (
    AuditLog,
    Gallery,
    GalleryStatus,
    Image,
    ImagePreview,
    ImageProcessingStatus,
    SelectionEvent,
    ShareSession,
    User,
)
from app.db.session import get_db
from app.frontend.deps import templates
from app.galleries.schemas import GUEST_MESSAGE_MAX_LENGTH, WatermarkConfig
from app.galleries.sharing import build_share_url
from app.i18n import t
from app.security.signing import sign_url
from app.selections.service import get_current_selections
from app.storage.base import StorageBackend
from app.storage.dependencies import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend-galleries"])

# Allowed status transitions (mirrors app/galleries/router.py)
ALLOWED_TRANSITIONS: dict[GalleryStatus, set[GalleryStatus]] = {
    GalleryStatus.draft: {GalleryStatus.shared},
    GalleryStatus.shared: {GalleryStatus.completed},
    GalleryStatus.completed: {GalleryStatus.archived, GalleryStatus.shared},
    GalleryStatus.archived: {GalleryStatus.shared},
}


class _ExpiryInPastError(Exception):
    """Raised when a gallery expiry date is set to a past or current instant."""


def _validate_future_expiry(raw: str) -> datetime:
    """Parse an ISO expiry timestamp and reject non-future values.

    The HTML ``datetime-local`` input yields a naive ISO string; we treat a
    naive value as UTC (matching how it is persisted into the ``timezone=True``
    column) so the comparison against ``now`` never mixes aware and naive
    datetimes. Raises ``ValueError`` on malformed input and ``_ExpiryInPastError``
    when the instant is not strictly in the future.
    """
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if parsed <= datetime.now(UTC):
        raise _ExpiryInPastError
    return parsed


async def _get_owned_gallery(gallery_id: uuid.UUID, user: User, db: AsyncSession) -> Gallery:
    """Load gallery with owner check. Raises 404 if not found or not owned."""
    result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return gallery


async def _load_images_with_signed_urls(gallery_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    """Load images with previews and generate signed URLs for thumbnails."""
    result = await db.execute(
        select(Image)
        .where(Image.gallery_id == gallery_id)
        .options(selectinload(Image.previews))
        .order_by(Image.sort_order)
    )
    images = result.scalars().all()

    image_list = []
    for img in images:
        previews = {}
        for preview in img.previews:
            previews[preview.variant.value] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)

        image_list.append(
            {
                "id": str(img.id),
                "filename": img.filename,
                "sort_order": img.sort_order,
                "width": img.width,
                "height": img.height,
                "previews": previews,
                "processing_status": img.processing_status.value,
            }
        )

    return image_list


async def _load_selection_map(gallery_id: uuid.UUID, db: AsyncSession) -> dict[str, dict[str, bool]]:
    """Build a compact {image_id: {selected, favorited}} map for the owner grid
    filter. Only images the model actually marked appear — unmarked images are
    absent and treated as neither selected nor favorited by the client filter.

    Gallery-wide (magic-link = one model), mirroring the guest viewer's sel_map.
    """
    selections = await get_current_selections(gallery_id, db)
    return {
        str(s.image_id): {"selected": s.selected, "favorited": s.favorited}
        for s in selections
        if s.selected or s.favorited
    }


def _build_context(
    request: Request,
    gallery: Gallery,
    images: list[dict[str, Any]],
    user: User,
    selection_map: dict[str, dict[str, bool]] | None = None,
    **extra: object,
) -> dict[str, Any]:
    """Build common template context for gallery pages."""
    allowed = ALLOWED_TRANSITIONS.get(gallery.status, set())
    transitions = []
    for target_status in allowed:
        # Cannot transition to shared without share token
        if target_status == GalleryStatus.shared and gallery.share_token_hash is None:
            continue
        # Re-sharing a finished or archived gallery re-opens it for the model;
        # label it "reopen" instead of the misleading generic "share".
        if target_status == GalleryStatus.shared and gallery.status in (
            GalleryStatus.completed,
            GalleryStatus.archived,
        ):
            label_key = "gallery.transition_reopen"
        else:
            label_key = f"gallery.transition_{target_status.value}"
        transitions.append(
            {
                "status": target_status.value,
                "label_key": label_key,
            }
        )

    # Slim, ready-only payload for the read-only lightbox (x4o). Pending/failed
    # images have no previews, so they are excluded; the grid still shows them.
    lightbox_images = [
        {
            "id": img["id"],
            "filename": img["filename"],
            "preview_url": img["previews"].get("preview") or img["previews"].get("thumb_md", ""),
            "thumb_md_url": img["previews"].get("thumb_md", ""),
        }
        for img in images
        if img["processing_status"] == "ready"
    ]

    ctx = {
        "request": request,
        "user": user,
        "gallery": gallery,
        "images": images,
        "lightbox_images": lightbox_images,
        "image_count": len(images),
        "transitions": transitions,
        "has_share_token": gallery.share_token_hash is not None,
        "csrf_token": request.cookies.get("csrf_token", ""),
        "selection_map": selection_map or {},
    }
    if gallery.share_token:
        ctx["share_url"] = build_share_url(request, gallery.share_token)
    ctx.update(extra)
    return ctx


@router.get("/galleries/{gallery_id}", response_class=HTMLResponse)
async def gallery_detail(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Gallery detail page with image grid, upload, share, and status controls."""
    gallery = await _get_owned_gallery(gallery_id, user, db)
    images = await _load_images_with_signed_urls(gallery_id, db)
    selection_map = await _load_selection_map(gallery_id, db)
    ctx = _build_context(request, gallery, images, user, selection_map=selection_map)
    return templates.TemplateResponse(request, "galleries/detail.html", ctx)


@router.get("/galleries/{gallery_id}/selection", response_class=HTMLResponse)
async def gallery_selection(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Read-only result view of the model's picks — every image the model
    selected OR favorited, with a Lightroom-ready filename list to copy or
    download (r84). Owner-only via the gallery ownership check."""
    gallery = await _get_owned_gallery(gallery_id, user, db)
    images = await _load_images_with_signed_urls(gallery_id, db)
    selections = await get_current_selections(gallery_id, db)
    state = {str(s.image_id): s for s in selections}

    items: list[dict[str, Any]] = []
    for img in images:
        s = state.get(img["id"])
        if s is None or not (s.selected or s.favorited):
            continue
        previews = img["previews"]
        items.append(
            {
                "filename": img["filename"],
                "thumb_url": previews.get("thumb_sm") or previews.get("thumb_md") or "",
                "selected": s.selected,
                "favorited": s.favorited,
                "comment": s.comment,
            }
        )

    # Comma-separated filename list for the "copy for Lightroom" button — built
    # server-side so the clipboard payload matches the txt download exactly.
    filename_list = ", ".join(item["filename"] for item in items)

    ctx = {
        "request": request,
        "user": user,
        "gallery": gallery,
        "items": items,
        "marked_count": len(items),
        "selected_count": sum(1 for i in items if i["selected"]),
        "favorited_count": sum(1 for i in items if i["favorited"]),
        "filename_list": filename_list,
        "csrf_token": request.cookies.get("csrf_token", ""),
    }
    return templates.TemplateResponse(request, "galleries/selection.html", ctx)


@router.post("/galleries/{gallery_id}/rename", response_class=HTMLResponse)
async def rename_gallery(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Rename gallery via form submission, re-render full detail page."""
    gallery = await _get_owned_gallery(gallery_id, user, db)
    form = await request.form()
    new_name = str(form.get("name", "")).strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="Gallery name is required")
    gallery.name = new_name
    await db.commit()
    await db.refresh(gallery)
    images = await _load_images_with_signed_urls(gallery_id, db)
    selection_map = await _load_selection_map(gallery_id, db)
    ctx = _build_context(request, gallery, images, user, selection_map=selection_map)
    return templates.TemplateResponse(request, "galleries/detail.html", ctx)


@router.get("/galleries/{gallery_id}/images-grid", response_class=HTMLResponse)
async def gallery_images_grid(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """HTMX partial: refreshed image grid."""
    gallery = await _get_owned_gallery(gallery_id, user, db)
    images = await _load_images_with_signed_urls(gallery_id, db)
    selection_map = await _load_selection_map(gallery_id, db)
    ctx = _build_context(request, gallery, images, user, selection_map=selection_map)
    return templates.TemplateResponse(request, "galleries/_image_grid.html", ctx)


@router.post("/galleries/{gallery_id}/upload", response_class=HTMLResponse)
async def upload_images(
    gallery_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(default_factory=list),
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> HTMLResponse:
    """Persist uploaded originals fast, queue preview generation, return the grid.

    Preview generation (Pillow resize + watermark + WebP) is CPU-bound and used to
    run inline here, freezing the UI for ~20s on large batches. Now the request only
    stores the original + an Image row (status=pending) and dispatches a background
    worker per image. The returned grid shows spinner tiles that poll until ready.
    """
    import io

    from app.images.preview_worker import process_image_previews
    from app.images.processing import (
        compute_sha256,
        extract_exif,
        get_image_dimensions,
    )
    from app.images.upload_limits import enforce_file_count, read_within_limit
    from app.storage.base import storage_key

    gallery = await _get_owned_gallery(gallery_id, user, db)

    logger.info(
        "Upload to gallery %s: received %d files (types: %s)",
        gallery_id,
        len(files),
        [getattr(f, "content_type", "?") for f in files],
    )

    # Filter out empty form parts (browsers can submit empty file fields).
    files = [f for f in files if f and f.filename]

    if not files:
        logger.warning("Upload to gallery %s: no usable files in form", gallery_id)
        raise HTTPException(status_code=422, detail="No files provided")

    enforce_file_count(len(files))

    allowed_types = {"image/jpeg", "image/png", "image/webp"}

    # Get current image count for sort_order offset
    existing_result = await db.execute(
        select(func.count()).select_from(Image).where(Image.gallery_id == gallery_id)
    )
    sort_offset = existing_result.scalar() or 0

    # Snapshot the gallery's watermark config now; the worker resolves text /
    # opacity / position / enabled from it (empty -> global default).
    watermark_config = gallery.watermark_config

    # Persist originals + Image rows only. Preview generation is dispatched to a
    # background worker (below) so the request returns immediately.
    queued: list[uuid.UUID] = []
    for idx, file in enumerate(files):
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type: {file.content_type}",
            )

        file_data = await read_within_limit(file)
        file_buf = io.BytesIO(file_data)

        sha256 = compute_sha256(file_buf)
        width, height = get_image_dimensions(file_buf)
        exif = extract_exif(file_buf)

        ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "jpg"
        image_uuid = uuid.uuid4()
        original_key = storage_key(str(gallery_id), "originals", f"{image_uuid}.{ext}")

        file_buf.seek(0)
        await storage.upload(original_key, file_buf, file.content_type or "image/jpeg")

        image = Image(
            id=image_uuid,
            gallery_id=gallery_id,
            storage_key=original_key,
            filename=file.filename or f"image_{idx}.{ext}",
            content_type=file.content_type or "image/jpeg",
            width=width,
            height=height,
            file_size=len(file_data),
            sha256=sha256,
            exif=exif,
            sort_order=sort_offset + idx,
            processing_status=ImageProcessingStatus.pending,
        )
        db.add(image)
        queued.append(image_uuid)

    await db.commit()

    # Dispatch preview generation after commit so the worker's own session sees
    # the committed rows. Each image is processed independently.
    for image_uuid in queued:
        background_tasks.add_task(process_image_previews, image_uuid, gallery_id, watermark_config)

    # Return refreshed image grid (queued images render as polling spinner tiles).
    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/_image_grid.html", ctx)


@router.post("/galleries/{gallery_id}/share", response_class=HTMLResponse)
async def create_share_link(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Generate a share link and return the share modal partial."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    password = str(form.get("password", "")).strip() or None

    token = secrets.token_urlsafe(32)
    token_hash, token_salt = hash_token(token)

    gallery.share_token_hash = token_hash
    gallery.share_token_salt = token_salt
    gallery.share_token = token

    if password:
        gallery.password_hash = hash_password(password)
    else:
        gallery.password_hash = None

    if gallery.status == GalleryStatus.draft:
        gallery.status = GalleryStatus.shared

    await db.commit()
    await db.refresh(gallery)

    share_url = build_share_url(request, token)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user, share_url=share_url)
    return templates.TemplateResponse(request, "galleries/_share_modal.html", ctx)


@router.delete("/galleries/{gallery_id}/share", response_class=HTMLResponse)
async def revoke_share_link(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Revoke the share link and return updated share modal partial."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    gallery.share_token_hash = None
    gallery.share_token_salt = None
    gallery.share_token = None
    gallery.password_hash = None

    if gallery.status == GalleryStatus.shared:
        gallery.status = GalleryStatus.draft

    await db.commit()
    await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/_share_modal.html", ctx)


@router.post("/galleries/{gallery_id}/password", response_class=HTMLResponse)
async def set_gallery_password(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Set, change or remove the gallery password without touching the share token.

    Re-sharing would rotate the token and kill the magic link already sent to
    the model; this endpoint only swaps the password hash. An empty password
    removes the protection (used by the explicit remove form in the modal).
    """
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    password = str(form.get("password", "")).strip() or None
    gallery.password_hash = hash_password(password) if password else None

    await db.commit()
    await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/_share_modal.html", ctx)


@router.post("/galleries/{gallery_id}/expiry", response_class=HTMLResponse)
async def set_gallery_expiry(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Set or clear the gallery expiration date."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    clear_expiry = str(form.get("clear_expiry", "")).strip()

    if clear_expiry:
        gallery.expires_at = None
    else:
        expires_at_raw = str(form.get("expires_at", "")).strip()
        if expires_at_raw:
            try:
                gallery.expires_at = _validate_future_expiry(expires_at_raw)
            except ValueError as err:
                raise HTTPException(status_code=422, detail="Ungültiges Datumsformat") from err
            except _ExpiryInPastError:
                # Reject silently-self-killing dates with an error toast and no
                # swap (an HTTPException would not surface — htmx ignores 4xx).
                locale = getattr(request.state, "locale", "de")
                resp = HTMLResponse("")
                message = t("gallery.expiry_must_be_future", locale)
                resp.headers["HX-Trigger"] = json.dumps({"showToast": {"kind": "error", "message": message}})
                resp.headers["HX-Reswap"] = "none"
                return resp
        # If empty string and no clear flag, do nothing (just re-render)

    await db.commit()
    await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/detail.html", ctx)


@router.post("/galleries/{gallery_id}/watermark", response_class=HTMLResponse)
async def set_gallery_watermark(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Set the per-gallery watermark text and on/off toggle (applies to new uploads)."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    text_raw = str(form.get("watermark_text", "")).strip()
    # Strip control chars / newlines so a pasted value can't break the rendered overlay.
    text_clean = "".join(ch for ch in text_raw if ch.isprintable())[:200]
    # A checkbox only submits its value when checked; absence means "off".
    enabled = form.get("watermark_enabled") is not None

    # Preserve any pre-existing position/opacity/font_size; only touch text + enabled.
    cfg = dict(gallery.watermark_config or {})
    cfg["enabled"] = enabled
    if text_clean:
        cfg["text"] = text_clean
    else:
        cfg.pop("text", None)  # empty -> fall back to the global default text

    # Validate + normalise via the same schema as create/update (enforces max_length).
    validated = WatermarkConfig(**cfg).model_dump(exclude_none=True)
    # Reassign a fresh dict so SQLAlchemy detects the JSON column change.
    gallery.watermark_config = validated or None

    await db.commit()
    await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/detail.html", ctx)


@router.post("/galleries/{gallery_id}/message", response_class=HTMLResponse)
async def set_gallery_message(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Set the optional free-text note shown to the model in the guest viewer (dii)."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    # Keep newlines (multi-line note) but drop other control chars; trim + cap length.
    raw = str(form.get("guest_message", ""))
    cleaned = "".join(ch for ch in raw if ch == "\n" or ch.isprintable()).strip()
    gallery.guest_message = cleaned[:GUEST_MESSAGE_MAX_LENGTH] or None

    await db.commit()
    await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/detail.html", ctx)


@router.post("/galleries/{gallery_id}/status", response_class=HTMLResponse)
async def transition_status(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Execute a status transition and return the full detail page (for HTMX swap)."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    target = str(form.get("status", "")).strip()

    try:
        target_status = GalleryStatus(target)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=f"Invalid status: {target}") from err

    if target_status == gallery.status:
        pass  # No-op, just re-render
    else:
        allowed = ALLOWED_TRANSITIONS.get(gallery.status, set())
        if target_status not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Transition from '{gallery.status.value}' to '{target_status.value}' is not allowed",
            )
        if target_status == GalleryStatus.shared and gallery.share_token_hash is None:
            raise HTTPException(
                status_code=409,
                detail="Cannot share gallery without a share link. Create one first.",
            )
        gallery.status = target_status
        await db.commit()
        await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/detail.html", ctx)


@router.post("/galleries/{gallery_id}/bulk-delete", response_class=HTMLResponse)
async def bulk_delete_images(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> HTMLResponse:
    """Bulk delete selected images and return refreshed image grid."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    image_ids_raw = form.getlist("image_ids")

    if not image_ids_raw:
        # Return grid as-is
        images = await _load_images_with_signed_urls(gallery_id, db)
        ctx = _build_context(request, gallery, images, user)
        return templates.TemplateResponse(request, "galleries/_image_grid.html", ctx)

    # Parse and validate UUIDs
    image_uuids = []
    for raw_id in image_ids_raw:
        try:
            image_uuids.append(uuid.UUID(str(raw_id)))
        except ValueError:
            continue

    if image_uuids:
        result = await db.execute(
            select(Image)
            .where(Image.id.in_(image_uuids), Image.gallery_id == gallery_id)
            .options(selectinload(Image.previews))
        )
        images_to_delete = result.scalars().all()

        for image in images_to_delete:
            try:
                await storage.delete(image.storage_key)
            except Exception:
                logger.warning("Failed to delete storage key %s", image.storage_key)
            for preview in image.previews:
                try:
                    await storage.delete(preview.storage_key)
                except Exception:
                    logger.warning("Failed to delete preview key %s", preview.storage_key)
            await db.delete(image)

        await db.commit()

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse(request, "galleries/_image_grid.html", ctx)


@router.post("/galleries/{gallery_id}/delete", response_class=HTMLResponse)
async def delete_gallery(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    """Delete a gallery after name confirmation. Redirects to dashboard."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    confirm_name = str(form.get("confirm_name", "")).strip()

    if confirm_name != gallery.name:
        # Name mismatch — re-render detail page with error
        images = await _load_images_with_signed_urls(gallery_id, db)
        ctx = _build_context(
            request,
            gallery,
            images,
            user,
            delete_error="gallery.delete_name_mismatch",
        )
        return templates.TemplateResponse(request, "galleries/detail.html", ctx)

    # 1. Audit log entry BEFORE deletion
    audit_entry = AuditLog(
        gallery_id=gallery.id,
        event_type="gallery_deleted",
        actor_user_id=user.id,
        details={"gallery_name": gallery.name},
    )
    db.add(audit_entry)
    await db.flush()

    # 2. Delete image files from storage (best-effort)
    result = await db.execute(select(Image).where(Image.gallery_id == gallery.id).options(selectinload(Image.previews)))
    images_to_delete = result.scalars().all()
    for image in images_to_delete:
        try:
            await storage.delete(image.storage_key)
        except Exception:
            logger.warning("Failed to delete storage file %s during gallery deletion", image.storage_key)
        for preview in image.previews:
            try:
                await storage.delete(preview.storage_key)
            except Exception:
                logger.warning("Failed to delete preview file %s during gallery deletion", preview.storage_key)

    # 3. Delete DB records in dependency order
    # Delete image_previews
    await db.execute(
        sa_delete(ImagePreview).where(ImagePreview.image_id.in_(select(Image.id).where(Image.gallery_id == gallery.id)))
    )
    # Delete selection_events
    await db.execute(
        sa_delete(SelectionEvent).where(
            SelectionEvent.image_id.in_(select(Image.id).where(Image.gallery_id == gallery.id))
        )
    )
    # Delete share_sessions
    await db.execute(sa_delete(ShareSession).where(ShareSession.gallery_id == gallery.id))
    # Anonymize audit_log entries
    await db.execute(
        sa_update(AuditLog).where(AuditLog.gallery_id == gallery.id).values(ip_address=None, user_agent=None)
    )
    # Detach audit_log from gallery
    await db.execute(sa_update(AuditLog).where(AuditLog.gallery_id == gallery.id).values(gallery_id=None))
    # Delete images
    await db.execute(sa_delete(Image).where(Image.gallery_id == gallery.id))
    # Delete gallery
    await db.execute(sa_delete(Gallery).where(Gallery.id == gallery.id))

    await db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/galleries/{gallery_id}/export")
async def export_redirect(
    gallery_id: uuid.UUID,
    request: Request,
    format: str = "csv",
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Redirect to the API export endpoint."""
    # Verify ownership first
    await _get_owned_gallery(gallery_id, user, db)
    return RedirectResponse(
        url=f"/api/v1/galleries/{gallery_id}/export?format={format}",
        status_code=302,
    )


# --- Audit Log ---


@router.get("/galleries/{gallery_id}/audit-log", response_class=HTMLResponse)
async def gallery_audit_log(
    gallery_id: uuid.UUID,
    request: Request,
    page: int = 1,
    event_type: str | None = None,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Audit log page for a gallery (owner only)."""
    import math

    gallery = await _get_owned_gallery(gallery_id, user, db)

    per_page = 50
    conditions = [AuditLog.gallery_id == gallery_id]
    if event_type:
        conditions.append(AuditLog.event_type == event_type)

    # Total count
    from sqlalchemy import func as sa_func

    count_result = await db.execute(select(sa_func.count()).select_from(AuditLog).where(*conditions))
    total = count_result.scalar() or 0
    total_pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, total_pages))

    # Fetch entries
    offset = (page - 1) * per_page
    result = await db.execute(
        select(AuditLog).where(*conditions).order_by(AuditLog.created_at.desc()).offset(offset).limit(per_page)
    )
    entries = result.scalars().all()

    # Get distinct event types for filter dropdown
    event_types_result = await db.execute(
        select(AuditLog.event_type).where(AuditLog.gallery_id == gallery_id).distinct().order_by(AuditLog.event_type)
    )
    event_types = [row[0] for row in event_types_result.all()]

    ctx = {
        "request": request,
        "user": user,
        "gallery": gallery,
        "entries": entries,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "event_type": event_type,
        "event_types": event_types,
        "csrf_token": request.cookies.get("csrf_token", ""),
    }

    # If HTMX request, return only the table partial
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "galleries/_audit_log_table.html", ctx)

    return templates.TemplateResponse(request, "galleries/audit_log.html", ctx)
