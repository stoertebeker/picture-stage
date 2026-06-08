import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_active_user
from app.auth.passwords import hash_password, hash_token
from app.config import settings
from app.db.models import Gallery, GalleryStatus, User
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/galleries", tags=["sharing"])


def build_share_url(request: Request, token: str) -> str:
    """Build the public share URL for a gallery token.

    Security: the share token is replayable and travels in the URL. Behind a
    TLS-terminating proxy (Cloudflare/Caddy) the container only ever sees plain
    HTTP, so ``request.base_url`` would yield ``http://`` and leak the token in
    clear text (MITM, proxy logs). We therefore prefer the operator-configured
    public ``APP_URL`` and, in production, force the scheme to https as a
    defense-in-depth net should APP_URL be missing or misconfigured.
    """
    base = (settings.app_url or str(request.base_url)).rstrip("/")
    if settings.environment.lower() == "production" and base.startswith("http://"):
        base = "https://" + base[len("http://") :]
    return f"{base}/g/{token}"


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
    gallery.share_token = token

    if body.password:
        gallery.password_hash = hash_password(body.password)
    else:
        gallery.password_hash = None

    if gallery.status == GalleryStatus.draft:
        gallery.status = GalleryStatus.shared

    await db.commit()

    return ShareResponse(
        share_url=build_share_url(request, token),
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
    gallery.share_token = None
    gallery.password_hash = None

    if gallery.status == GalleryStatus.shared:
        gallery.status = GalleryStatus.draft

    await db.commit()
