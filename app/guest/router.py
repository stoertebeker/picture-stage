import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.passwords import verify_password, verify_token
from app.db.models import (
    Gallery,
    GalleryStatus,
    Image,
    PreviewVariant,
    ShareSession,
)
from app.db.session import get_db
from app.security.signing import sign_url

router = APIRouter(prefix="/g", tags=["guest"])


class GuestGalleryResponse(BaseModel):
    gallery_id: uuid.UUID
    name: str
    image_count: int
    requires_password: bool
    session_id: uuid.UUID | None = None


class PasswordVerifyRequest(BaseModel):
    password: str


class GuestImageResponse(BaseModel):
    id: uuid.UUID
    filename: str
    sort_order: int
    thumb_sm_url: str
    thumb_md_url: str
    preview_url: str


async def _resolve_gallery_by_token(token: str, db: AsyncSession) -> Gallery | None:
    result = await db.execute(
        select(Gallery).where(
            Gallery.share_token_hash.isnot(None),
            Gallery.status.in_([GalleryStatus.shared, GalleryStatus.completed]),
        )
    )
    galleries = result.scalars().all()

    for gallery in galleries:
        if gallery.share_token_hash and gallery.share_token_salt:
            if verify_token(token, gallery.share_token_hash, gallery.share_token_salt):
                return gallery
    return None


def _check_gallery_accessible(gallery: Gallery) -> None:
    if gallery.expires_at and gallery.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Gallery link has expired")


@router.get("/{token}", response_model=GuestGalleryResponse)
async def get_shared_gallery(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> GuestGalleryResponse:
    gallery = await _resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    _check_gallery_accessible(gallery)

    image_count_result = await db.execute(
        select(Image).where(Image.gallery_id == gallery.id)
    )
    image_count = len(image_count_result.scalars().all())

    requires_password = gallery.password_hash is not None

    session_id = None
    if not requires_password:
        session = ShareSession(
            gallery_id=gallery.id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:512],
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        session_id = session.id

    return GuestGalleryResponse(
        gallery_id=gallery.id,
        name=gallery.name,
        image_count=image_count,
        requires_password=requires_password,
        session_id=session_id,
    )


@router.post("/{token}/verify-password", response_model=GuestGalleryResponse)
async def verify_gallery_password(
    token: str,
    body: PasswordVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> GuestGalleryResponse:
    gallery = await _resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    _check_gallery_accessible(gallery)

    if not gallery.password_hash or not verify_password(body.password, gallery.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    session = ShareSession(
        gallery_id=gallery.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    image_count_result = await db.execute(
        select(Image).where(Image.gallery_id == gallery.id)
    )
    image_count = len(image_count_result.scalars().all())

    return GuestGalleryResponse(
        gallery_id=gallery.id,
        name=gallery.name,
        image_count=image_count,
        requires_password=True,
        session_id=session.id,
    )


@router.get("/{token}/images", response_model=list[GuestImageResponse])
async def list_shared_images(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> list[GuestImageResponse]:
    gallery = await _resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    _check_gallery_accessible(gallery)

    result = await db.execute(
        select(Image)
        .where(Image.gallery_id == gallery.id)
        .options(selectinload(Image.previews))
        .order_by(Image.sort_order)
    )
    images = result.scalars().all()

    guest_images = []
    for img in images:
        preview_urls: dict[str, str] = {}
        for preview in img.previews:
            if preview.variant == PreviewVariant.thumb_sm:
                preview_urls["thumb_sm"] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)
            elif preview.variant == PreviewVariant.thumb_md:
                preview_urls["thumb_md"] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)
            elif preview.variant == PreviewVariant.preview:
                preview_urls["preview"] = sign_url(f"/media/{preview.storage_key}", expires_in=900)

        guest_images.append(GuestImageResponse(
            id=img.id,
            filename=img.filename,
            sort_order=img.sort_order,
            thumb_sm_url=preview_urls.get("thumb_sm", ""),
            thumb_md_url=preview_urls.get("thumb_md", ""),
            preview_url=preview_urls.get("preview", ""),
        ))

    return guest_images
