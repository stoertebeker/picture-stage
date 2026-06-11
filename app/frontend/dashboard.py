"""Frontend dashboard: gallery list with status and progress."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_user_from_cookie, require_authenticated_page
from app.db.models import (
    Gallery,
    Image,
    ImagePreview,
    PreviewVariant,
    SelectionAction,
    SelectionEvent,
    ShareSession,
    User,
)
from app.db.session import get_db
from app.frontend.deps import templates
from app.galleries.quota import GalleryQuotaExceeded, assert_within_gallery_quota
from app.i18n import t
from app.security.signing import sign_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend"])


@router.get("/", response_class=HTMLResponse)
async def root_redirect(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Redirect / to /setup if no users, /dashboard if authenticated, /login otherwise."""
    user_count_result = await db.execute(select(func.count()).select_from(User))
    if (user_count_result.scalar() or 0) == 0:
        return RedirectResponse(url="/setup", status_code=302)
    user = await get_user_from_cookie(request, db)
    if user is not None:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Dashboard page: gallery list with status and progress."""
    galleries = await _load_gallery_data(user, db)

    csrf_token = request.cookies.get("csrf_token", "")
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "request": request,
            "user": user,
            "galleries": galleries,
            "csrf_token": csrf_token,
        },
    )


@router.post("/dashboard/galleries", response_class=HTMLResponse)
async def create_gallery(
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Create a new gallery via HTMX, return gallery card partial."""
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Gallery name is required")

    locale = getattr(request.state, "locale", "de")
    try:
        await assert_within_gallery_quota(user.id, db)
    except GalleryQuotaExceeded as exc:
        # Show an error toast without swapping a card into the grid (HX-Reswap: none).
        resp = HTMLResponse("")
        message = t("gallery.limit_reached", locale, limit=exc.limit)
        resp.headers["HX-Trigger"] = json.dumps({"showToast": {"kind": "error", "message": message}})
        resp.headers["HX-Reswap"] = "none"
        return resp

    gallery = Gallery(owner_id=user.id, name=name)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    gallery_data = {
        "gallery": gallery,
        "image_count": 0,
        "selected_count": 0,
        "favorited_count": 0,
        "commented_count": 0,
        "last_activity": None,
        "cover_url": None,
    }

    csrf_token = request.cookies.get("csrf_token", "")
    response = templates.TemplateResponse(
        request,
        "dashboard/_gallery_card.html",
        {
            "request": request,
            "g": gallery_data,
            "csrf_token": csrf_token,
        },
    )
    # Toast notification (ps-ux-13).
    if locale == "de":
        message = f"Galerie {name!r} angelegt."
    else:
        message = f"Gallery {name!r} created."
    response.headers["HX-Trigger"] = json.dumps({"showToast": {"kind": "success", "message": message}})
    return response


async def _load_gallery_data(user: User, db: AsyncSession) -> list[dict[str, Any]]:
    """Load galleries with image counts and selection progress for the dashboard."""
    result = await db.execute(select(Gallery).where(Gallery.owner_id == user.id).order_by(Gallery.updated_at.desc()))
    galleries = result.scalars().all()

    gallery_data: list[dict[str, Any]] = []
    for gallery in galleries:
        # Image count
        img_count_result = await db.execute(
            select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id)
        )
        image_count = img_count_result.scalar() or 0

        # Selection counts
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

        # Last activity
        last_access_result = await db.execute(
            select(func.max(ShareSession.started_at)).where(ShareSession.gallery_id == gallery.id)
        )
        last_activity = last_access_result.scalar()
        cover_url = await _load_gallery_cover_url(gallery, db)

        gallery_data.append(
            {
                "gallery": gallery,
                "image_count": image_count,
                "selected_count": selected_count,
                "favorited_count": favorited_count,
                "commented_count": commented_count,
                "last_activity": last_activity,
                "cover_url": cover_url,
            }
        )

    return gallery_data


async def _load_gallery_cover_url(gallery: Gallery, db: AsyncSession) -> str | None:
    """Return a signed medium thumbnail URL for the first image in a gallery."""
    result = await db.execute(
        select(ImagePreview.storage_key)
        .join(Image, ImagePreview.image_id == Image.id)
        .where(Image.gallery_id == gallery.id, ImagePreview.variant == PreviewVariant.thumb_md)
        .order_by(Image.sort_order)
        .limit(1)
    )
    storage_key = result.scalar_one_or_none()
    if storage_key is None:
        result = await db.execute(
            select(ImagePreview.storage_key)
            .join(Image, ImagePreview.image_id == Image.id)
            .where(Image.gallery_id == gallery.id, ImagePreview.variant == PreviewVariant.thumb_sm)
            .order_by(Image.sort_order)
            .limit(1)
        )
        storage_key = result.scalar_one_or_none()
    if storage_key is None:
        return None
    return sign_url(f"/media/{storage_key}", expires_in=3600)
