import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_active_user
from app.auth.passwords import hash_password, hash_token
from app.db.models import Gallery, GalleryStatus, User
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/galleries", tags=["sharing"])


class ShareCreateRequest(BaseModel):
    password: str | None = None


class ShareResponse(BaseModel):
    share_url: str
    has_password: bool


@router.post("/{gallery_id}/share", response_model=ShareResponse)
async def create_share_link(
    gallery_id: str,
    body: ShareCreateRequest,
    request: Request,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ShareResponse:
    result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    token = secrets.token_urlsafe(32)
    token_hash, token_salt = hash_token(token)

    gallery.share_token_hash = token_hash
    gallery.share_token_salt = token_salt

    if body.password:
        gallery.password_hash = hash_password(body.password)
    else:
        gallery.password_hash = None

    if gallery.status == GalleryStatus.draft:
        gallery.status = GalleryStatus.shared

    await db.commit()

    base_url = str(request.base_url).rstrip("/")
    return ShareResponse(
        share_url=f"{base_url}/g/{token}",
        has_password=body.password is not None,
    )


@router.delete("/{gallery_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(
    gallery_id: str,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    gallery.share_token_hash = None
    gallery.share_token_salt = None
    gallery.password_hash = None

    if gallery.status == GalleryStatus.shared:
        gallery.status = GalleryStatus.draft

    await db.commit()
