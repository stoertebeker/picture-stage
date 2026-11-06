"""Frontend dashboard: gallery list with status and progress."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_user_from_cookie, require_authenticated_page
from app.db.models import Gallery, User
from app.db.session import get_db
from app.frontend.deps import templates
from app.galleries.dashboard import load_dashboard_galleries
from app.galleries.quota import GalleryQuotaExceeded, get_gallery_limit, reserve_gallery_slot
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
            **_gallery_quota_context(user, len(galleries)),
            "csrf_token": csrf_token,
        },
    )


@router.post("/dashboard/galleries", response_class=HTMLResponse)
async def create_gallery(
    request: Request,
    user: User = Depends(require_authenticated_page),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Create a new gallery via HTMX, return gallery card partial."""
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Gallery name is required")

    locale = getattr(request.state, "locale", "de")
    try:
        await reserve_gallery_slot(user.id, db)
    except GalleryQuotaExceeded:
        # A second tab may have used the last slot. Refresh both the persistent
        # notice and the gallery list instead of closing the dialog as success.
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Refresh": "true"})
        return RedirectResponse(url="/dashboard", status_code=303)

    gallery = Gallery(owner_id=user.id, name=name)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)
    await db.refresh(user, attribute_names=["gallery_limit_override"])
    gallery_count = await db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == user.id))

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
        "dashboard/_created_gallery.html",
        {
            "request": request,
            "g": gallery_data,
            **_gallery_quota_context(user, gallery_count or 0),
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


def _gallery_quota_context(user: User, gallery_count: int) -> dict[str, Any]:
    limit = get_gallery_limit(user.gallery_limit_override)
    return {
        "gallery_count": gallery_count,
        "gallery_limit": limit,
        "gallery_limit_reached": limit > 0 and gallery_count >= limit,
    }


async def _load_gallery_data(user: User, db: AsyncSession) -> list[dict[str, Any]]:
    """Adapt batched gallery summaries to the dashboard template context."""
    summaries = await load_dashboard_galleries(user.id, db, order_by="updated_at", include_covers=True)
    return [
        {
            "gallery": summary.gallery,
            "image_count": summary.image_count,
            "selected_count": summary.selected_count,
            "favorited_count": summary.favorited_count,
            "commented_count": summary.commented_count,
            "last_activity": summary.last_activity,
            "cover_url": (
                sign_url(f"/media/{summary.cover_storage_key}", expires_in=3600)
                if summary.cover_storage_key is not None
                else None
            ),
        }
        for summary in summaries
    ]
