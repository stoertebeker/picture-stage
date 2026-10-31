import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_active_user
from app.db.models import Gallery, Image, ImagePreview, PreviewVariant, User
from app.db.session import get_db
from app.galleries.lifecycle import lock_gallery, lock_images
from app.images.process_pool import run_in_pool
from app.images.processing import (
    PREVIEW_SIZES,
    InvalidImageContentError,
    extract_image_metadata,
    render_preview_bytes,
    render_thumbnail_bytes,
)
from app.images.schemas import BulkDeleteRequest, BulkDeleteResponse, ImageResponse, ImageUploadResponse
from app.images.storage_cleanup import delete_storage_keys, image_storage_keys
from app.images.upload_batch import UploadBatch, UploadPlan
from app.images.upload_limits import enforce_file_count, invalid_image_error, read_within_limit
from app.security.signing import verify_signed_url
from app.storage.base import StorageBackend, storage_key
from app.storage.dependencies import get_storage

router = APIRouter(tags=["images"])

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _image_to_response(image: Image, storage: StorageBackend) -> ImageResponse:
    previews = {}
    for preview in image.previews:
        previews[preview.variant.value] = f"/media/{preview.storage_key}"

    return ImageResponse(
        id=image.id,
        filename=image.filename,
        content_type=image.content_type,
        width=image.width,
        height=image.height,
        file_size=image.file_size,
        sort_order=image.sort_order,
        created_at=image.created_at,
        previews=previews,
    )


@router.post(
    "/api/v1/galleries/{gallery_id}/images",
    response_model=ImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_images(
    gallery_id: uuid.UUID,
    files: list[UploadFile],
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> ImageUploadResponse:
    await lock_gallery(db, gallery_id, shared=True)
    gal_result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = gal_result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    enforce_file_count(len(files))
    for file in files:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported file type: {file.content_type}",
            )

    plans = [UploadPlan.for_file(gallery_id, file.content_type, previews=True) for file in files]
    async with UploadBatch(db, storage, gallery_id, plans) as batch:
        gal_result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
        gallery = gal_result.scalar_one_or_none()
        if gallery is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

        existing_count_result = await db.execute(
            select(func.count()).select_from(Image).where(Image.gallery_id == gallery_id)
        )
        sort_offset = existing_count_result.scalar() or 0

        uploaded_images: list[ImageResponse] = []
        # Resolve text / opacity / position / enabled from the gallery's own config
        # (empty -> global default); honour an explicit watermark-disabled setting.
        watermark_config = gallery.watermark_config

        for idx, (file, plan) in enumerate(zip(files, plans, strict=True)):
            file_data = await read_within_limit(file)
            file_buf = io.BytesIO(file_data)

            try:
                sha256, width, height, exif = await run_in_pool(extract_image_metadata, file_data)
            except InvalidImageContentError as exc:
                raise invalid_image_error() from exc

            ext = plan.extension
            image_uuid = plan.image_id
            original_key = plan.original_key

            file_buf.seek(0)
            await batch.upload(original_key, file_buf, file.content_type or "image/jpeg")

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
                try:
                    if variant_name == "preview":
                        preview_bytes, pw, ph = await run_in_pool(
                            render_preview_bytes,
                            file_data,
                            max_width,
                            watermark_config,
                            str(gallery.id),
                        )
                    else:
                        preview_bytes, pw, ph = await run_in_pool(render_thumbnail_bytes, file_data, max_width)
                except InvalidImageContentError as exc:
                    raise invalid_image_error() from exc

                preview_key = storage_key(str(gallery_id), "previews", f"{image_uuid}_{variant_name}.webp")
                preview_buf = io.BytesIO(preview_bytes)
                await batch.upload(preview_key, preview_buf, "image/webp")

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

            img_result = await db.execute(
                select(Image).where(Image.id == image_uuid).options(selectinload(Image.previews))
            )
            loaded_image = img_result.scalar_one()
            uploaded_images.append(_image_to_response(loaded_image, storage))

        await batch.commit()

    return ImageUploadResponse(uploaded=len(uploaded_images), images=uploaded_images)


@router.get("/api/v1/galleries/{gallery_id}/images", response_model=list[ImageResponse])
async def list_images(
    gallery_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> list[ImageResponse]:
    gal_check = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    if gal_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    img_result = await db.execute(
        select(Image)
        .where(Image.gallery_id == gallery_id)
        .options(selectinload(Image.previews))
        .order_by(Image.sort_order)
    )
    images = img_result.scalars().all()

    return [_image_to_response(img, storage) for img in images]


@router.delete("/api/v1/galleries/{gallery_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    gallery_id: uuid.UUID,
    image_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> None:
    await lock_gallery(db, gallery_id, shared=True)
    gal_check = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    if gal_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    await lock_images(db, gallery_id, [image_id])
    img_result = await db.execute(
        select(Image).where(Image.id == image_id, Image.gallery_id == gallery_id).options(selectinload(Image.previews))
    )
    image = img_result.scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    await delete_storage_keys(storage, image_storage_keys(image))

    await db.delete(image)
    await db.commit()


@router.post(
    "/api/v1/galleries/{gallery_id}/images/bulk-delete",
    response_model=BulkDeleteResponse,
)
async def bulk_delete_images(
    gallery_id: uuid.UUID,
    body: BulkDeleteRequest,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> BulkDeleteResponse:
    await lock_gallery(db, gallery_id)
    gal_check = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    if gal_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    if not body.image_ids:
        return BulkDeleteResponse(deleted=0)

    img_result = await db.execute(
        select(Image)
        .where(Image.id.in_(body.image_ids), Image.gallery_id == gallery_id)
        .options(selectinload(Image.previews))
    )
    images = img_result.scalars().all()

    for image in images:
        await delete_storage_keys(storage, image_storage_keys(image))
        await db.delete(image)

    await db.commit()
    return BulkDeleteResponse(deleted=len(images))


@router.get("/media/{key:path}")
async def serve_media(
    key: str,
    exp: int = Query(...),
    sig: str = Query(...),
    storage: StorageBackend = Depends(get_storage),
) -> StreamingResponse:
    if not verify_signed_url(f"/media/{key}", exp, sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired URL")

    if not await storage.exists(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    content_type = "image/webp"
    if key.endswith(".jpg") or key.endswith(".jpeg"):
        content_type = "image/jpeg"
    elif key.endswith(".png"):
        content_type = "image/png"

    return StreamingResponse(
        storage.download_stream(key),
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
