import logging
import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Gallery,
    GalleryStatus,
    Image,
    SelectionAction,
    SelectionEvent,
    User,
)
from app.db.session import get_db
from app.galleries.lifecycle import lock_gallery, lock_images
from app.guest.credentials import private_guest_response, request_session_id
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.guest.service import (
    check_gallery_accessible,
    create_share_session,
    current_share_session,
    load_sorted_filtered_images,
    lock_gallery_for_review,
    lock_gallery_for_session_admission,
    require_gallery_read_access,
    resolve_gallery_by_token,
    sign_preview_urls,
)
from app.notifications.service import notify_owner_gallery_completed, send_notification
from app.security.rate_limit import limiter
from app.selections.schemas import SelectionEventCreate, SelectionSummary
from app.selections.service import (
    SelectionHistoryFull,
    assert_selection_budget,
    get_current_selections,
    lock_selection_budget,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/g", tags=["guest"], dependencies=[Depends(private_guest_response)])


class GuestGalleryResponse(BaseModel):
    gallery_id: uuid.UUID
    name: str
    image_count: int
    requires_password: bool
    session_id: uuid.UUID | None = None


class PasswordVerifyRequest(BaseModel):
    password: str


class CompleteReviewRequest(BaseModel):
    session_id: uuid.UUID | None = None


class GuestImageResponse(BaseModel):
    id: uuid.UUID
    filename: str
    sort_order: int
    thumb_sm_url: str
    thumb_md_url: str
    preview_url: str


class CompleteReviewResponse(BaseModel):
    message: str
    gallery_status: GalleryStatus
    session_completed: bool


@router.get("/{token}", response_model=GuestGalleryResponse)
async def get_shared_gallery(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> GuestGalleryResponse:
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return cast(GuestGalleryResponse, await _get_resolved_gallery(token=token, request=request, gallery=gallery, db=db))


@limiter.limit("20/10minutes")
async def _get_resolved_gallery(
    token: str, request: Request, gallery: Gallery, db: AsyncSession
) -> GuestGalleryResponse:

    check_gallery_accessible(gallery)
    # Session producers must never hold FOR SHARE before admission/cleanup.
    gallery = await lock_gallery_for_session_admission(token, gallery.id, db)

    image_count_result = await db.execute(select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id))
    image_count = image_count_result.scalar() or 0

    requires_password = gallery.password_hash is not None

    session_id = None
    if not requires_password:
        gallery, session = await create_share_session(token, gallery.id, request, db)
        session_id = session.id

    response = GuestGalleryResponse(
        gallery_id=gallery.id,
        name=gallery.name,
        image_count=image_count,
        requires_password=requires_password,
        session_id=session_id,
    )
    await db.commit()
    return response


@router.post("/{token}/verify-password", response_model=GuestGalleryResponse)
async def verify_gallery_password(
    token: str,
    body: PasswordVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> GuestGalleryResponse:
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return cast(
        GuestGalleryResponse,
        await _verify_resolved_gallery_password(token=token, body=body, request=request, gallery=gallery, db=db),
    )


@limiter.limit("5/minute")
async def _verify_resolved_gallery_password(
    token: str, body: PasswordVerifyRequest, request: Request, gallery: Gallery, db: AsyncSession
) -> GuestGalleryResponse:

    check_gallery_accessible(gallery)

    gallery, session = await create_share_session(token, gallery.id, request, db, password=body.password)

    image_count_result = await db.execute(select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id))
    image_count = image_count_result.scalar() or 0

    response = GuestGalleryResponse(
        gallery_id=gallery.id,
        name=gallery.name,
        image_count=image_count,
        requires_password=True,
        session_id=session.id,
    )
    await db.commit()
    return response


@router.get("/{token}/images", response_model=list[GuestImageResponse])
async def list_shared_images(
    token: str,
    request: Request,
    sort_by: ImageSortBy = Query(ImageSortBy.sort_order),
    sort_dir: SortDirection = Query(SortDirection.asc),
    filter: ImageFilter = Query(ImageFilter.all),
    db: AsyncSession = Depends(get_db),
) -> list[GuestImageResponse]:
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    check_gallery_accessible(gallery)
    session_id = request_session_id(request, gallery.id)
    gallery = await require_gallery_read_access(gallery, session_id, db, token=token)

    # Selection map is unused here (the JSON API omits selection fields), but the
    # shared loader always materialises it from a single query (d7z).
    images, _sel_map = await load_sorted_filtered_images(gallery, db, sort_by, sort_dir, filter)

    guest_images = []
    for img in images:
        preview_urls = sign_preview_urls(img)
        guest_images.append(
            GuestImageResponse(
                id=img.id,
                filename=img.filename,
                sort_order=img.sort_order,
                thumb_sm_url=preview_urls.get("thumb_sm", ""),
                thumb_md_url=preview_urls.get("thumb_md", ""),
                preview_url=preview_urls.get("preview", ""),
            )
        )

    await db.commit()
    return guest_images


@router.post("/{token}/selections", status_code=status.HTTP_201_CREATED)
async def create_selection_event(
    token: str,
    body: SelectionEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    check_gallery_accessible(gallery)

    session_id = request_session_id(request, gallery.id, body=body)
    await lock_gallery(db, gallery.id, shared=True)
    await lock_images(db, gallery.id, [body.image_id])
    gallery = await db.scalar(select(Gallery).where(Gallery.id == gallery.id).execution_options(populate_existing=True))
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    check_gallery_accessible(gallery)

    image_result = await db.execute(select(Image).where(Image.id == body.image_id, Image.gallery_id == gallery.id))
    if image_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found in this gallery")

    # Read-only once the review is completed (gallery-wide, magic-link = one model).
    if gallery.status == GalleryStatus.completed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Review already completed")

    # Session is still resolved for event attribution / audit (one row per visit).
    session = await current_share_session(gallery, session_id, db)
    if session is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active session")

    await lock_selection_budget(gallery.id, db)
    gallery = await lock_gallery_for_review(token, gallery.id, db)
    if gallery.status == GalleryStatus.completed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Review already completed")
    session = await current_share_session(gallery, session_id, db)
    if session is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active session")
    try:
        await assert_selection_budget(gallery.id, db)
    except SelectionHistoryFull as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Selection history limit reached") from exc

    event = SelectionEvent(
        image_id=body.image_id,
        share_session_id=session.id,
        action=body.action,
        comment=body.comment,
    )
    db.add(event)
    await db.commit()

    return {"status": "ok"}


@router.get("/{token}/selections", response_model=SelectionSummary)
async def get_selections(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SelectionSummary:
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    check_gallery_accessible(gallery)
    session_id = request_session_id(request, gallery.id)
    gallery = await require_gallery_read_access(gallery, session_id, db, token=token)

    selections = await get_current_selections(gallery.id, db)

    image_count_result = await db.execute(select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id))
    total = image_count_result.scalar() or 0

    response = SelectionSummary(
        total_images=total,
        selected_count=sum(1 for s in selections if s.selected),
        favorited_count=sum(1 for s in selections if s.favorited),
        commented_count=sum(1 for s in selections if s.comment is not None),
        selections=selections,
    )
    await db.commit()
    return response


@router.post("/{token}/complete", response_model=CompleteReviewResponse)
async def complete_review(
    token: str,
    body: CompleteReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CompleteReviewResponse:
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    check_gallery_accessible(gallery)

    session_id = request_session_id(request, gallery.id, body=body)
    await lock_gallery(db, gallery.id, shared=True)
    gallery = await lock_gallery_for_review(token, gallery.id, db)
    session = await current_share_session(gallery, session_id, db)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if gallery.status == GalleryStatus.completed or session.completed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review already completed",
        )

    session.completed_at = datetime.now(UTC)

    if gallery.status == GalleryStatus.shared:
        gallery.status = GalleryStatus.completed

    await db.commit()

    try:
        img_count = await db.execute(select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id))
        total_images = img_count.scalar() or 0

        sel_counts = await db.execute(
            select(
                func.count(func.distinct(SelectionEvent.image_id)).filter(
                    SelectionEvent.action == SelectionAction.select
                ),
                func.count(func.distinct(SelectionEvent.image_id)).filter(
                    SelectionEvent.action == SelectionAction.favorite
                ),
            )
            .join(Image, SelectionEvent.image_id == Image.id)
            .where(Image.gallery_id == gallery.id)
        )
        row = sel_counts.one()

        payload = {
            "gallery_name": gallery.name,
            "total_images": total_images,
            "selected_count": row[0],
            "favorited_count": row[1],
            "gallery_url": f"{settings.app_url}/galleries/{gallery.id}",
        }

        # Config-gated path (webhooks / future NotificationConfig UI).
        await send_notification(
            event_type="gallery_completed",
            user_id=str(gallery.owner_id),
            payload=payload,
            db=db,
        )

        # Guaranteed email to the photographer (config-free, bypasses the empty
        # NotificationConfig table). Recipient is the owner's own address.
        owner_email = await db.scalar(select(User.email).where(User.id == gallery.owner_id))
        if owner_email:
            await notify_owner_gallery_completed(owner_email, payload, db)
    except Exception:
        logger.exception("Failed to send gallery_completed notification")

    return CompleteReviewResponse(
        message="Review completed. Thank you!",
        gallery_status=gallery.status,
        session_completed=True,
    )
