"""Frontend guest router - serves HTML gallery viewer for share-link visitors."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Gallery,
    GalleryStatus,
)
from app.db.session import get_db
from app.frontend.deps import templates
from app.guest.credentials import (
    PRIVATE_HEADERS,
    browser_session,
    password_challenge,
    private_guest_response,
    request_session_id,
    set_browser_session,
    set_password_challenge,
    verify_password_csrf,
)
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.guest.service import (
    check_gallery_accessible,
    create_share_session,
    is_gallery_expired,
    load_sorted_filtered_images,
    require_gallery_read_access,
    resolve_gallery_by_token,
    sign_preview_urls,
)
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/g", tags=["frontend-guest"], dependencies=[Depends(private_guest_response)])


# Number of grid items rendered per page. The full image list is still sent to
# the client (data-images) for the lightbox/selection state, but the heavy grid
# DOM is loaded progressively to keep the initial HTML small (picture-stage-am9).
GALLERY_PAGE_SIZE = 30


async def _load_images(
    gallery: Gallery,
    db: AsyncSession,
    sort_by: ImageSortBy = ImageSortBy.sort_order,
    sort_dir: SortDirection = SortDirection.asc,
    filter_mode: ImageFilter = ImageFilter.all,
) -> list[dict[str, Any]]:
    """Load images for a gallery with sorting, filtering, and signed URLs.

    Thin adapter over the shared loader (d7z): keeps the dict shape the templates
    expect (incl. per-image selection state for the lightbox/grid).
    """
    images, sel_map = await load_sorted_filtered_images(gallery, db, sort_by, sort_dir, filter_mode)

    image_list = []
    for img in images:
        preview_urls = sign_preview_urls(img)
        sel = sel_map.get(img.id)
        image_list.append(
            {
                "id": str(img.id),
                "filename": img.filename,
                "sort_order": img.sort_order,
                "thumb_sm_url": preview_urls.get("thumb_sm", ""),
                "thumb_md_url": preview_urls.get("thumb_md", ""),
                "preview_url": preview_urls.get("preview", ""),
                "selected": sel.selected if sel else False,
                "favorited": sel.favorited if sel else False,
                "comment": sel.comment if sel else None,
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

    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    if is_gallery_expired(gallery):
        return templates.TemplateResponse(
            request,
            "guest/expired.html",
            {"request": request, "gallery_name": gallery.name},
            status_code=410,
            headers=PRIVATE_HEADERS,
        )

    if gallery.password_hash is not None:
        # Show password gate — no session until the password is verified
        return _render_password_gate(request, gallery, token)

    return await _render_gallery_viewer(request, gallery, token, db)


def _render_password_gate(
    request: Request, gallery: Gallery, token: str, error_key: str | None = None, status_code: int = 200
) -> HTMLResponse:
    """Render the password gate full-page (qdz.16), optionally with an error alert."""
    challenge, csrf = password_challenge(request, gallery.id)
    response = templates.TemplateResponse(
        request,
        "guest/viewer.html",
        {
            "request": request,
            "gallery_name": gallery.name,
            "token": token,
            "requires_password": True,
            "error_key": error_key,
            "all_images": [],
            "image_order": [],
            "images": [],
            "guest_csrf_token": csrf,
            "total_images": 0,
            "selected_count": 0,
            "favorited_count": 0,
            "sort_by": "sort_order",
            "sort_dir": "asc",
            "filter": "all",
        },
        status_code=status_code,
        headers=PRIVATE_HEADERS,
    )
    set_password_challenge(response, request, token, gallery.id, challenge)
    return response


async def _render_gallery_viewer(
    request: Request, gallery: Gallery, token: str, db: AsyncSession, *, password: str | None = None
) -> HTMLResponse:
    """Create a share session and render the full gallery viewer page."""
    try:
        gallery, session = await create_share_session(
            token, gallery.id, request, db, password=password, reuse_browser_session=True
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            response = _render_password_gate(
                request,
                gallery,
                token,
                error_key="guest.password_error" if password is not None else None,
                status_code=status.HTTP_401_UNAUTHORIZED if password is not None else status.HTTP_200_OK,
            )
        elif exc.status_code == 410 and password is None:
            response = templates.TemplateResponse(
                request,
                "guest/expired.html",
                {"request": request, "gallery_name": gallery.name},
                status_code=410,
                headers=PRIVATE_HEADERS,
            )
        else:
            raise
        await db.commit()
        return response

    cookie, csrf = browser_session(gallery.id, session.id, request=request)
    images = await _load_images(gallery, db)
    total = len(images)
    selected_count = sum(1 for img in images if img["selected"])
    favorited_count = sum(1 for img in images if img["favorited"])

    response = templates.TemplateResponse(
        request,
        "guest/viewer.html",
        {
            "request": request,
            "gallery_name": gallery.name,
            "gallery_message": gallery.guest_message,
            "token": token,
            "requires_password": False,
            # Full list for the Alpine state (data-images): lightbox + selection
            # need every image. The grid renders only the first page (am9).
            "all_images": images,
            # Ordered id list the lightbox navigates (rfii). Initially unfiltered
            # in sort_order; replaced on each filter/sort grid refresh so the
            # lightbox walks exactly the visible subset in the grid's order.
            "image_order": [img["id"] for img in images],
            "images": images[:GALLERY_PAGE_SIZE],
            "append_mode": False,
            "next_offset": GALLERY_PAGE_SIZE,
            "has_more": total > GALLERY_PAGE_SIZE,
            "guest_csrf_token": csrf,
            "total_images": total,
            "selected_count": selected_count,
            "favorited_count": favorited_count,
            "session_completed": gallery.status == GalleryStatus.completed,
            "max_selection_comment_chars": settings.max_selection_comment_chars,
            "sort_by": "sort_order",
            "sort_dir": "asc",
            "filter": "all",
        },
    )
    await db.commit()
    set_browser_session(response, request, token, gallery.id, cookie)
    return response


@router.get("/{token}/gallery-images", response_class=HTMLResponse)
async def guest_gallery_images(
    token: str,
    request: Request,
    sort_by: ImageSortBy = Query(ImageSortBy.sort_order),
    sort_dir: SortDirection = Query(SortDirection.asc),
    filter: ImageFilter = Query(ImageFilter.all),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """HTMX partial: image grid page.

    Serves two cases with the same fragment (grid items + optional infinite-scroll
    sentinel): a sort/filter refresh (offset=0, swaps #image-grid) and progressive
    loading (offset>0, the sentinel replaces itself with the next page) — am9.
    """
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    check_gallery_accessible(gallery)
    session_id = request_session_id(request, gallery.id)
    gallery = await require_gallery_read_access(gallery, session_id, db, token=token)

    images = await _load_images(gallery, db, sort_by, sort_dir, filter)
    total = len(images)
    selected_count = sum(1 for img in images if img["selected"])
    favorited_count = sum(1 for img in images if img["favorited"])
    page = images[offset : offset + GALLERY_PAGE_SIZE]
    # Full ordered id list of the current (filtered+sorted) view so the lightbox
    # navigates the visible subset (rfii). Only consumed on a filter/sort refresh
    # (offset==0, swaps #image-grid); the infinite-scroll path (offset>0) leaves
    # the order unchanged and the afterSwap handler ignores it.
    image_order = [img["id"] for img in images] if offset == 0 else None

    response = templates.TemplateResponse(
        request,
        "guest/_image_grid.html",
        {
            "request": request,
            "images": page,
            # A progressive load replaces the sentinel itself (outerHTML), so
            # its response must be bare sibling tiles rather than another grid
            # wrapper. Filter/sort refreshes still replace #image-grid and need
            # the wrapper (picture-stage-4k2g).
            "append_mode": offset > 0,
            "image_order": image_order,
            "token": token,
            "total_images": total,
            "selected_count": selected_count,
            "favorited_count": favorited_count,
            "session_completed": gallery.status == GalleryStatus.completed,
            "sort_by": sort_by.value,
            "sort_dir": sort_dir.value,
            "filter": filter.value,
            "next_offset": offset + GALLERY_PAGE_SIZE,
            "has_more": total > offset + GALLERY_PAGE_SIZE,
        },
        headers=PRIVATE_HEADERS,
    )
    await db.commit()
    return response


@router.post("/{token}/verify-password", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def guest_verify_password(
    token: str,
    request: Request,
    password: str = Form(...),
    guest_csrf_token: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Verify the gallery password (plain form POST, qdz.16).

    Success renders the full viewer page so header, counters, lightbox and the
    Alpine images state are all present (the previous HTMX grid-only swap left
    the page without them). Failure re-renders the gate with an error alert,
    mirroring the login form pattern (full page, 401).
    """
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    check_gallery_accessible(gallery)

    verify_password_csrf(request, gallery.id, guest_csrf_token)
    return await _render_gallery_viewer(request, gallery, token, db, password=password)
