"""Frontend guest router - serves HTML gallery viewer for share-link visitors."""

import enum
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
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
from app.frontend.deps import templates
from app.security.signing import sign_url
from app.selections.service import get_current_selections

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/g", tags=["frontend-guest"])


class ImageSortBy(enum.StrEnum):
    sort_order = "sort_order"
    filename = "filename"
    exif_date = "exif_date"


class SortDirection(enum.StrEnum):
    asc = "asc"
    desc = "desc"


class ImageFilter(enum.StrEnum):
    all = "all"
    selected = "selected"
    favorited = "favorited"
    unrated = "unrated"


async def _resolve_gallery_by_token(token: str, db: AsyncSession) -> Gallery | None:
    """Resolve a share token to a Gallery, checking hash against all shared galleries."""
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
    """Raise 410 if gallery link has expired."""
    if gallery.expires_at and gallery.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Gallery link has expired")


def _parse_exif_date(exif: dict | None) -> datetime | None:
    """Parse EXIF date from image metadata."""
    if not exif:
        return None
    raw = exif.get("DateTimeOriginal") or exif.get("DateTime")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


async def _load_images(
    gallery: Gallery,
    db: AsyncSession,
    sort_by: ImageSortBy = ImageSortBy.sort_order,
    sort_dir: SortDirection = SortDirection.asc,
    filter_mode: ImageFilter = ImageFilter.all,
    session_id: uuid.UUID | None = None,
) -> list[dict]:
    """Load images for a gallery with sorting, filtering, and signed URLs."""
    order_col = Image.sort_order
    if sort_by == ImageSortBy.filename:
        order_col = Image.filename
    order_clause = order_col.desc() if sort_dir == SortDirection.desc else order_col.asc()

    result = await db.execute(
        select(Image).where(Image.gallery_id == gallery.id).options(selectinload(Image.previews)).order_by(order_clause)
    )
    images = list(result.scalars().all())

    if sort_by == ImageSortBy.exif_date:
        far_future = datetime(9999, 1, 1)
        images.sort(
            key=lambda img: _parse_exif_date(img.exif) or far_future,
            reverse=(sort_dir == SortDirection.desc),
        )

    # Build selection map for filtering
    sel_map: dict[uuid.UUID, dict] = {}
    if session_id is not None:
        selections = await get_current_selections(gallery.id, session_id, db)
        sel_map = {
            s.image_id: {"selected": s.selected, "favorited": s.favorited, "comment": s.comment} for s in selections
        }

    if filter_mode != ImageFilter.all and session_id is not None:
        if filter_mode == ImageFilter.selected:
            images = [img for img in images if sel_map.get(img.id, {}).get("selected")]
        elif filter_mode == ImageFilter.favorited:
            images = [img for img in images if sel_map.get(img.id, {}).get("favorited")]
        elif filter_mode == ImageFilter.unrated:
            images = [
                img
                for img in images
                if not sel_map.get(img.id, {}).get("selected") and not sel_map.get(img.id, {}).get("favorited")
            ]

    image_list = []
    for img in images:
        preview_urls: dict[str, str] = {}
        for preview in img.previews:
            if preview.variant == PreviewVariant.thumb_sm:
                preview_urls["thumb_sm"] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)
            elif preview.variant == PreviewVariant.thumb_md:
                preview_urls["thumb_md"] = sign_url(f"/media/{preview.storage_key}", expires_in=3600)
            elif preview.variant == PreviewVariant.preview:
                preview_urls["preview"] = sign_url(f"/media/{preview.storage_key}", expires_in=900)

        sel = sel_map.get(img.id, {"selected": False, "favorited": False, "comment": None})
        image_list.append(
            {
                "id": str(img.id),
                "filename": img.filename,
                "sort_order": img.sort_order,
                "thumb_sm_url": preview_urls.get("thumb_sm", ""),
                "thumb_md_url": preview_urls.get("thumb_md", ""),
                "preview_url": preview_urls.get("preview", ""),
                "selected": sel.get("selected", False),
                "favorited": sel.get("favorited", False),
                "comment": sel.get("comment"),
            }
        )

    return image_list


def _wants_html(request: Request) -> bool:
    """Check if the request wants an HTML response (browser request)."""
    accept = request.headers.get("accept", "")
    # API clients send Authorization header or explicitly request JSON
    if request.headers.get("authorization"):
        return False
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


@router.get("/{token}", response_class=HTMLResponse)
async def guest_viewer(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Serve the guest gallery viewer page.

    If the gallery requires a password and no session exists, show the password form.
    Otherwise, render the full gallery viewer with images and selection state.
    """
    if not _wants_html(request):
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail="Use JSON API")

    gallery = await _resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    _check_gallery_accessible(gallery)

    requires_password = gallery.password_hash is not None

    if requires_password:
        # Show password prompt — no session yet
        return templates.TemplateResponse(
            "guest/viewer.html",
            {
                "request": request,
                "gallery_name": gallery.name,
                "token": token,
                "requires_password": True,
                "images": [],
                "session_id": None,
                "total_images": 0,
                "selected_count": 0,
                "favorited_count": 0,
                "sort_by": "sort_order",
                "sort_dir": "asc",
                "filter": "all",
            },
        )

    # No password required — create session and show gallery
    session = ShareSession(
        gallery_id=gallery.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    images = await _load_images(gallery, db, session_id=session.id)
    total = len(images)
    selected_count = sum(1 for img in images if img["selected"])
    favorited_count = sum(1 for img in images if img["favorited"])

    return templates.TemplateResponse(
        "guest/viewer.html",
        {
            "request": request,
            "gallery_name": gallery.name,
            "token": token,
            "requires_password": False,
            "images": images,
            "session_id": str(session.id),
            "total_images": total,
            "selected_count": selected_count,
            "favorited_count": favorited_count,
            "sort_by": "sort_order",
            "sort_dir": "asc",
            "filter": "all",
        },
    )


@router.get("/{token}/gallery-images", response_class=HTMLResponse)
async def guest_gallery_images(
    token: str,
    request: Request,
    sort_by: ImageSortBy = Query(ImageSortBy.sort_order),
    sort_dir: SortDirection = Query(SortDirection.asc),
    filter: ImageFilter = Query(ImageFilter.all),
    session_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """HTMX partial: return the image grid for sort/filter refresh."""
    gallery = await _resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    _check_gallery_accessible(gallery)

    images = await _load_images(gallery, db, sort_by, sort_dir, filter, session_id)
    total = len(images)
    selected_count = sum(1 for img in images if img["selected"])
    favorited_count = sum(1 for img in images if img["favorited"])

    return templates.TemplateResponse(
        "guest/_image_grid.html",
        {
            "request": request,
            "images": images,
            "token": token,
            "session_id": str(session_id) if session_id else None,
            "total_images": total,
            "selected_count": selected_count,
            "favorited_count": favorited_count,
            "sort_by": sort_by.value,
            "sort_dir": sort_dir.value,
            "filter": filter.value,
        },
    )


@router.post("/{token}/verify-password", response_class=HTMLResponse)
async def guest_verify_password(
    token: str,
    request: Request,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Verify gallery password and swap in the image grid via HTMX."""
    gallery = await _resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    _check_gallery_accessible(gallery)

    if not gallery.password_hash or not verify_password(password, gallery.password_hash):
        return templates.TemplateResponse(
            "guest/_password.html",
            {
                "request": request,
                "token": token,
                "error": "Falsches Passwort",
            },
        )

    session = ShareSession(
        gallery_id=gallery.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    images = await _load_images(gallery, db, session_id=session.id)
    total = len(images)
    selected_count = sum(1 for img in images if img["selected"])
    favorited_count = sum(1 for img in images if img["favorited"])

    return templates.TemplateResponse(
        "guest/_image_grid.html",
        {
            "request": request,
            "images": images,
            "token": token,
            "session_id": str(session.id),
            "total_images": total,
            "selected_count": selected_count,
            "favorited_count": favorited_count,
            "sort_by": "sort_order",
            "sort_dir": "asc",
            "filter": "all",
        },
    )
