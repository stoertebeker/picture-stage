from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_active_user
from app.config import settings
from app.db.models import User
from app.db.session import get_db
from app.galleries.management import create_gallery_share, get_owned_gallery, revoke_gallery_share
from app.galleries.password_hashing import hash_gallery_password

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
    gallery = await get_owned_gallery(gallery_id, user, db)
    password_hash = await hash_gallery_password(body.password) if body.password else None
    gallery, token = await create_gallery_share(gallery.id, user.id, db, password_hash=password_hash)

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
    gallery = await get_owned_gallery(gallery_id, user, db)
    await revoke_gallery_share(gallery.id, user.id, db)

    await db.commit()
