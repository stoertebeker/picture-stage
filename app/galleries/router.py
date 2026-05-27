import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_active_user
from app.db.models import (
    Gallery,
    GalleryStatus,
    Image,
    ImagePreview,
    PendingSignup,
    SelectionAction,
    SelectionEvent,
    ShareSession,
    User,
    UserStatus,
)
from app.db.session import get_db
from app.galleries.schemas import (
    DashboardGalleryResponse,
    DashboardResponse,
    GalleryCreate,
    GalleryDuplicateRequest,
    GalleryListResponse,
    GalleryResponse,
    GalleryStatusTransition,
    GalleryUpdate,
)
from app.storage.base import StorageBackend, storage_key
from app.storage.dependencies import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/galleries", tags=["galleries"])


def _gallery_to_response(gallery: Gallery, image_count: int) -> GalleryResponse:
    return GalleryResponse(
        id=gallery.id,
        name=gallery.name,
        phase=gallery.phase,
        status=gallery.status,
        watermark_config=gallery.watermark_config,
        expires_at=gallery.expires_at,
        has_share_token=gallery.share_token_hash is not None,
        image_count=image_count,
        created_at=gallery.created_at,
        updated_at=gallery.updated_at,
    )


async def _get_owned_gallery(
    gallery_id: uuid.UUID, user: User, db: AsyncSession
) -> Gallery:
    result = await db.execute(
        select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id)
    )
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return gallery


async def _count_images(gallery_id: uuid.UUID, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(Image).where(Image.gallery_id == gallery_id)
    )
    return result.scalar() or 0


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    gallery_result = await db.execute(
        select(Gallery).where(Gallery.owner_id == user.id).order_by(Gallery.created_at.desc())
    )
    galleries = gallery_result.scalars().all()

    dashboard_galleries: list[DashboardGalleryResponse] = []
    for gallery in galleries:
        img_count_result = await db.execute(
            select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id)
        )
        image_count = img_count_result.scalar() or 0

        sel_counts = await db.execute(
            select(
                func.count(func.distinct(SelectionEvent.image_id)).filter(
                    SelectionEvent.action == SelectionAction.select
                ),
                func.count(func.distinct(SelectionEvent.image_id)).filter(
                    SelectionEvent.action == SelectionAction.favorite
                ),
                func.count(func.distinct(SelectionEvent.image_id)).filter(
                    SelectionEvent.action == SelectionAction.comment
                ),
            )
            .join(Image, SelectionEvent.image_id == Image.id)
            .where(Image.gallery_id == gallery.id)
        )
        row = sel_counts.one()
        selected_count, favorited_count, commented_count = row[0], row[1], row[2]

        last_access_result = await db.execute(
            select(func.max(ShareSession.started_at)).where(
                ShareSession.gallery_id == gallery.id
            )
        )
        last_activity = last_access_result.scalar()

        dashboard_galleries.append(
            DashboardGalleryResponse(
                id=gallery.id,
                name=gallery.name,
                status=gallery.status,
                image_count=image_count,
                selected_count=selected_count,
                favorited_count=favorited_count,
                commented_count=commented_count,
                has_share_token=gallery.share_token_hash is not None,
                last_activity=last_activity,
                created_at=gallery.created_at,
            )
        )

    pending_signups_count = None
    if user.status == UserStatus.admin:
        pending_result = await db.execute(
            select(func.count()).select_from(PendingSignup)
        )
        pending_signups_count = pending_result.scalar() or 0

    return DashboardResponse(
        galleries=dashboard_galleries,
        total_galleries=len(dashboard_galleries),
        pending_signups_count=pending_signups_count,
    )


@router.post("", response_model=GalleryResponse, status_code=http_status.HTTP_201_CREATED)
async def create_gallery(
    body: GalleryCreate,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> GalleryResponse:
    gallery = Gallery(
        owner_id=user.id,
        name=body.name,
        watermark_config=body.watermark_config,
    )
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    return _gallery_to_response(gallery, image_count=0)


@router.get("", response_model=list[GalleryListResponse])
async def list_galleries(
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[GalleryListResponse]:
    stmt = (
        select(Gallery, func.count(Image.id).label("image_count"))
        .outerjoin(Image, Image.gallery_id == Gallery.id)
        .where(Gallery.owner_id == user.id)
        .group_by(Gallery.id)
        .order_by(Gallery.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        GalleryListResponse(
            id=gallery.id,
            name=gallery.name,
            status=gallery.status,
            image_count=count,
            created_at=gallery.created_at,
        )
        for gallery, count in rows
    ]


@router.get("/{gallery_id}", response_model=GalleryResponse)
async def get_gallery(
    gallery_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> GalleryResponse:
    gallery = await _get_owned_gallery(gallery_id, user, db)
    image_count = await _count_images(gallery_id, db)
    return _gallery_to_response(gallery, image_count)


@router.patch("/{gallery_id}", response_model=GalleryResponse)
async def update_gallery(
    gallery_id: uuid.UUID,
    body: GalleryUpdate,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> GalleryResponse:
    gallery = await _get_owned_gallery(gallery_id, user, db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(gallery, field, value)

    await db.commit()
    await db.refresh(gallery)

    image_count = await _count_images(gallery_id, db)
    return _gallery_to_response(gallery, image_count)


@router.delete("/{gallery_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_gallery(
    gallery_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> None:
    gallery = await _get_owned_gallery(gallery_id, user, db)

    # Delete images from storage
    result = await db.execute(select(Image).where(Image.gallery_id == gallery.id))
    images = result.scalars().all()
    for image in images:
        try:
            await storage.delete(image.storage_key)
        except Exception:  # noqa: S110
            pass
        for preview in await image.awaitable_attrs.previews:
            try:
                await storage.delete(preview.storage_key)
            except Exception:  # noqa: S110
                pass

    await db.delete(gallery)
    await db.commit()


ALLOWED_TRANSITIONS: dict[GalleryStatus, set[GalleryStatus]] = {
    GalleryStatus.draft: {GalleryStatus.shared},
    GalleryStatus.shared: {GalleryStatus.completed},
    GalleryStatus.completed: {GalleryStatus.archived, GalleryStatus.shared},
    GalleryStatus.archived: {GalleryStatus.shared},
}


@router.patch("/{gallery_id}/status", response_model=GalleryResponse)
async def transition_gallery_status(
    gallery_id: uuid.UUID,
    body: GalleryStatusTransition,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> GalleryResponse:
    gallery = await _get_owned_gallery(gallery_id, user, db)

    if body.status == gallery.status:
        image_count = await _count_images(gallery_id, db)
        return _gallery_to_response(gallery, image_count)

    allowed = ALLOWED_TRANSITIONS.get(gallery.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Transition from '{gallery.status.value}' to '{body.status.value}' is not allowed",
        )

    if body.status == GalleryStatus.shared and gallery.share_token_hash is None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Cannot share gallery without a share link. Create one first.",
        )

    gallery.status = body.status
    await db.commit()
    await db.refresh(gallery)

    image_count = await _count_images(gallery_id, db)
    return _gallery_to_response(gallery, image_count)


@router.post(
    "/{gallery_id}/duplicate",
    response_model=GalleryResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def duplicate_gallery(
    gallery_id: uuid.UUID,
    body: GalleryDuplicateRequest,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> GalleryResponse:
    source = await _get_owned_gallery(gallery_id, user, db)

    new_gallery = Gallery(
        owner_id=user.id,
        name=body.name or f"{source.name} (Kopie)",
        watermark_config=source.watermark_config,
    )
    db.add(new_gallery)
    await db.flush()

    result = await db.execute(
        select(Image)
        .where(Image.gallery_id == source.id)
        .options(selectinload(Image.previews))
        .order_by(Image.sort_order)
    )
    source_images = result.scalars().all()

    for img in source_images:
        new_image_id = uuid.uuid4()
        ext = img.storage_key.rsplit(".", 1)[-1] if "." in img.storage_key else "jpg"
        new_original_key = storage_key(str(new_gallery.id), "originals", f"{new_image_id}.{ext}")

        try:
            await storage.copy(img.storage_key, new_original_key)
        except Exception:
            logger.warning("Failed to copy image %s, skipping", img.id)
            continue

        new_image = Image(
            id=new_image_id,
            gallery_id=new_gallery.id,
            storage_key=new_original_key,
            filename=img.filename,
            content_type=img.content_type,
            width=img.width,
            height=img.height,
            file_size=img.file_size,
            sha256=img.sha256,
            exif=img.exif,
            sort_order=img.sort_order,
        )
        db.add(new_image)

        for preview in img.previews:
            new_preview_key = storage_key(
                str(new_gallery.id), "previews", f"{new_image_id}_{preview.variant.value}.webp"
            )
            try:
                await storage.copy(preview.storage_key, new_preview_key)
            except Exception:
                logger.warning("Failed to copy preview %s, skipping", preview.storage_key)
                continue

            new_preview = ImagePreview(
                image_id=new_image_id,
                variant=preview.variant,
                storage_key=new_preview_key,
                width=preview.width,
                height=preview.height,
                file_size=preview.file_size,
            )
            db.add(new_preview)

    await db.commit()
    await db.refresh(new_gallery)

    image_count = await _count_images(new_gallery.id, db)
    return _gallery_to_response(new_gallery, image_count)
