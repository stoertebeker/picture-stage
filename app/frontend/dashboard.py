"""Frontend dashboard: gallery list with status and progress."""

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
    SelectionAction,
    SelectionEvent,
    ShareSession,
    User,
)
from app.db.session import get_db
from app.frontend.deps import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend"])


@router.get("/", response_class=HTMLResponse)
async def root_redirect(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Redirect / to /dashboard if authenticated, /login if not."""
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
    }

    csrf_token = request.cookies.get("csrf_token", "")
    return templates.TemplateResponse(
        request,
        "dashboard/_gallery_card.html",
        {
            "request": request,
            "g": gallery_data,
            "csrf_token": csrf_token,
        },
    )


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

        gallery_data.append(
            {
                "gallery": gallery,
                "image_count": image_count,
                "selected_count": selected_count,
                "favorited_count": favorited_count,
                "commented_count": commented_count,
                "last_activity": last_activity,
            }
        )

    return gallery_data
