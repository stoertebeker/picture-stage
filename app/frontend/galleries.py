"""Frontend gallery management: detail, upload, share, status transitions."""

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_authenticated_page
from app.auth.passwords import hash_password, hash_token
from app.db.models import Gallery, GalleryStatus, Image, ImagePreview, PreviewVariant, User
from app.db.session import get_db
from app.frontend.deps import templates
from app.security.signing import sign_url
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

# Human-readable status labels
STATUS_LABELS: dict[GalleryStatus, str] = {
    GalleryStatus.draft: "Entwurf",
    GalleryStatus.shared: "Geteilt",
    GalleryStatus.completed: "Abgeschlossen",
    GalleryStatus.archived: "Archiviert",
}

# Human-readable transition action labels
TRANSITION_LABELS: dict[GalleryStatus, str] = {
    GalleryStatus.shared: "Teilen",
    GalleryStatus.completed: "Abschliessen",
    GalleryStatus.archived: "Archivieren",
}


async def _get_owned_gallery(
    gallery_id: uuid.UUID, user: User, db: AsyncSession
) -> Gallery:
    """Load gallery with owner check. Raises 404 if not found or not owned."""
    result = await db.execute(
        select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id)
    )
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return gallery


async def _load_images_with_signed_urls(
    gallery_id: uuid.UUID, db: AsyncSession
) -> list[dict]:
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

        image_list.append({
            "id": str(img.id),
            "filename": img.filename,
            "sort_order": img.sort_order,
            "width": img.width,
            "height": img.height,
            "previews": previews,
        })

    return image_list


def _build_context(
    request: Request,
    gallery: Gallery,
    images: list[dict],
    user: User,
    **extra: object,
) -> dict:
    """Build common template context for gallery pages."""
    allowed = ALLOWED_TRANSITIONS.get(gallery.status, set())
    transitions = []
    for target_status in allowed:
        # Cannot transition to shared without share token
        if target_status == GalleryStatus.shared and gallery.share_token_hash is None:
            continue
        transitions.append({
            "status": target_status.value,
            "label": TRANSITION_LABELS.get(target_status, target_status.value),
        })

    ctx = {
        "request": request,
        "user": user,
        "gallery": gallery,
        "images": images,
        "image_count": len(images),
        "status_label": STATUS_LABELS.get(gallery.status, gallery.status.value),
        "transitions": transitions,
        "has_share_token": gallery.share_token_hash is not None,
        "csrf_token": request.cookies.get("csrf_token", ""),
    }
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
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse("galleries/detail.html", ctx)


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
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse("galleries/detail.html", ctx)


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
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse("galleries/_image_grid.html", ctx)


@router.post("/galleries/{gallery_id}/upload", response_class=HTMLResponse)
async def upload_images(
    gallery_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> HTMLResponse:
    """Handle image upload and return refreshed image grid partial."""
    import io

    from app.images.processing import (
        PREVIEW_SIZES,
        compute_sha256,
        extract_exif,
        generate_preview_with_watermark,
        generate_thumbnail,
        get_image_dimensions,
    )
    from app.storage.base import storage_key

    gallery = await _get_owned_gallery(gallery_id, user, db)

    form = await request.form()
    files = form.getlist("files")

    if not files:
        raise HTTPException(status_code=422, detail="No files provided")

    allowed_types = {"image/jpeg", "image/png", "image/webp"}

    # Get current image count for sort_order offset
    existing_result = await db.execute(
        select(Image).where(Image.gallery_id == gallery_id)
    )
    sort_offset = len(existing_result.scalars().all())

    watermark_text = f"PREVIEW · {str(gallery.id)[:8].upper()}"

    for idx, file in enumerate(files):
        if not hasattr(file, "read"):
            continue
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type: {file.content_type}",
            )

        file_data = await file.read()
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
        )
        db.add(image)

        for variant_name, max_width in PREVIEW_SIZES.items():
            file_buf.seek(0)
            if variant_name == "preview":
                preview_buf, pw, ph = generate_preview_with_watermark(
                    file_buf, max_width, watermark_text
                )
            else:
                preview_buf, pw, ph = generate_thumbnail(file_buf, max_width)

            preview_key = storage_key(
                str(gallery_id), "previews", f"{image_uuid}_{variant_name}.webp"
            )
            await storage.upload(preview_key, preview_buf, "image/webp")

            preview = ImagePreview(
                image_id=image_uuid,
                variant=PreviewVariant(variant_name),
                storage_key=preview_key,
                width=pw,
                height=ph,
                file_size=preview_buf.getbuffer().nbytes,
            )
            db.add(preview)

        await db.flush()

    await db.commit()

    # Return refreshed image grid
    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse("galleries/_image_grid.html", ctx)


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

    if password:
        gallery.password_hash = hash_password(password)
    else:
        gallery.password_hash = None

    if gallery.status == GalleryStatus.draft:
        gallery.status = GalleryStatus.shared

    await db.commit()
    await db.refresh(gallery)

    base_url = str(request.base_url).rstrip("/")
    share_url = f"{base_url}/g/{token}"

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user, share_url=share_url)
    return templates.TemplateResponse("galleries/_share_modal.html", ctx)


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
    gallery.password_hash = None

    if gallery.status == GalleryStatus.shared:
        gallery.status = GalleryStatus.draft

    await db.commit()
    await db.refresh(gallery)

    images = await _load_images_with_signed_urls(gallery_id, db)
    ctx = _build_context(request, gallery, images, user)
    return templates.TemplateResponse("galleries/_share_modal.html", ctx)


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
    return templates.TemplateResponse("galleries/detail.html", ctx)


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
        return templates.TemplateResponse("galleries/_image_grid.html", ctx)

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
    return templates.TemplateResponse("galleries/_image_grid.html", ctx)


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
