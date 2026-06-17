import csv
import io
import logging
import math
import re
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_active_user
from app.db.models import (
    ALLOWED_TRANSITIONS,
    AuditLog,
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
from app.galleries.deletion import purge_gallery
from app.galleries.quota import GalleryQuotaExceeded, assert_within_gallery_quota
from app.galleries.schemas import (
    AuditLogEntry,
    AuditLogResponse,
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


def _sanitize_filename(name: str) -> str:
    """Strip a user-supplied name down to chars safe for a Content-Disposition
    filename. Allows word chars, plain space, and hyphen only — excludes CR/LF
    and other whitespace to prevent header injection (\\s would keep newlines)."""
    return re.sub(r"[^\w -]", "", name).strip()


def _gallery_to_response(gallery: Gallery, image_count: int) -> GalleryResponse:
    return GalleryResponse(
        id=gallery.id,
        name=gallery.name,
        guest_message=gallery.guest_message,
        phase=gallery.phase,
        status=gallery.status,
        watermark_config=gallery.watermark_config,
        expires_at=gallery.expires_at,
        has_share_token=gallery.share_token_hash is not None,
        image_count=image_count,
        created_at=gallery.created_at,
        updated_at=gallery.updated_at,
    )


async def _get_owned_gallery(gallery_id: uuid.UUID, user: User, db: AsyncSession) -> Gallery:
    result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return gallery


async def _count_images(gallery_id: uuid.UUID, db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(Image).where(Image.gallery_id == gallery_id))
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
            select(func.max(ShareSession.started_at)).where(ShareSession.gallery_id == gallery.id)
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
                expires_at=gallery.expires_at,
                last_activity=last_activity,
                created_at=gallery.created_at,
            )
        )

    pending_signups_count = None
    if user.status == UserStatus.admin:
        pending_result = await db.execute(select(func.count()).select_from(PendingSignup))
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
    try:
        await assert_within_gallery_quota(user.id, db, limit_override=user.gallery_limit_override)
    except GalleryQuotaExceeded as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Gallery limit reached ({exc.limit})",
        ) from exc

    gallery = Gallery(
        owner_id=user.id,
        name=body.name,
        guest_message=body.guest_message,
        watermark_config=body.watermark_config,
        expires_at=body.expires_at,
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
            expires_at=gallery.expires_at,
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

    # Write audit log entry BEFORE deletion; purge_gallery anonymises + detaches it.
    audit_entry = AuditLog(
        gallery_id=gallery.id,
        event_type="gallery_deleted",
        actor_user_id=user.id,
        details={"gallery_name": gallery.name},
    )
    db.add(audit_entry)
    await db.flush()

    await purge_gallery(gallery, db, storage)
    await db.commit()


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
        guest_message=source.guest_message,
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


# --- Audit Log ---


@router.get("/{gallery_id}/audit-log", response_model=AuditLogResponse)
async def get_audit_log(
    gallery_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    event_type: str | None = Query(None),
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> AuditLogResponse:
    """Return paginated audit log entries for a gallery (owner only)."""
    # Tenant isolation: verify gallery ownership
    await _get_owned_gallery(gallery_id, user, db)

    # Base filter
    conditions = [AuditLog.gallery_id == gallery_id]
    if event_type:
        conditions.append(AuditLog.event_type == event_type)

    # Total count
    count_stmt = select(func.count()).select_from(AuditLog).where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    total_pages = max(1, math.ceil(total / per_page))

    # Fetch page
    offset = (page - 1) * per_page
    stmt = select(AuditLog).where(*conditions).order_by(AuditLog.created_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return AuditLogResponse(
        entries=[
            AuditLogEntry(
                id=e.id,
                event_type=e.event_type,
                actor_user_id=e.actor_user_id,
                actor_session_id=e.actor_session_id,
                ip_address=e.ip_address,
                user_agent=e.user_agent,
                details=e.details,
                created_at=e.created_at,
            )
            for e in entries
        ],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get("/{gallery_id}/audit-log/export")
async def export_audit_log(
    gallery_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Export all audit log entries for a gallery as CSV (owner only)."""
    gallery = await _get_owned_gallery(gallery_id, user, db)

    stmt = select(AuditLog).where(AuditLog.gallery_id == gallery_id).order_by(AuditLog.created_at.desc())
    result = await db.execute(stmt)
    entries = result.scalars().all()

    def generate_csv() -> Iterator[str]:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "event_type",
                "actor_user_id",
                "actor_session_id",
                "ip_address",
                "user_agent",
                "details",
                "created_at",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for entry in entries:
            writer.writerow(
                [
                    entry.id,
                    entry.event_type,
                    str(entry.actor_user_id) if entry.actor_user_id else "",
                    str(entry.actor_session_id) if entry.actor_session_id else "",
                    entry.ip_address or "",
                    entry.user_agent or "",
                    str(entry.details) if entry.details else "",
                    entry.created_at.isoformat(),
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    safe_name = _sanitize_filename(gallery.name)
    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-log-{safe_name}.csv"'},
    )
