"""Frontend guest router - serves HTML gallery viewer for share-link visitors."""

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Gallery,
    GalleryStatus,
    ShareSession,
)
from app.db.session import get_db
from app.frontend.deps import templates
from app.guest.credentials import (
    PRIVATE_HEADERS,
    browser_cookie_session_id,
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
    MAX_CURSOR_LENGTH,
    GuestImagePage,
    check_gallery_accessible,
    create_share_session,
    current_share_session,
    is_gallery_expired,
    load_guest_image_page,
    load_sorted_filtered_images,
    require_gallery_read_access,
    resolve_gallery_by_token,
    sign_preview_urls,
)
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/g", tags=["frontend-guest"], dependencies=[Depends(private_guest_response)])


# Guest images and lightbox data are both loaded one bounded page at a time.
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

    return _serialize_images(images, sel_map)


def _serialize_images(images: list[Any], sel_map: dict[Any, Any]) -> list[dict[str, Any]]:
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


def _page_context(
    page: GuestImagePage,
    sort_by: ImageSortBy,
    sort_dir: SortDirection,
    filter_mode: ImageFilter,
    *,
    append_mode: bool = False,
) -> dict[str, Any]:
    images = _serialize_images(page.images, page.selections)
    metadata = {
        "images": images,
        "next_cursor": page.next_cursor,
        "query": {"sort_by": sort_by.value, "sort_dir": sort_dir.value, "filter": filter_mode.value},
        "total_images": page.total_images,
        "selected_count": page.selected_count,
        "favorited_count": page.favorited_count,
        "filtered_count": page.filtered_count,
        "append": append_mode,
    }
    return {
        "images": images,
        "all_images": images,
        "image_order": [img["id"] for img in images],
        "page_metadata": metadata,
        "append_mode": append_mode,
        "next_cursor": page.next_cursor,
        "has_more": page.next_cursor is not None,
        "total_images": page.total_images,
        "selected_count": page.selected_count,
        "favorited_count": page.favorited_count,
        "filtered_count": page.filtered_count,
        "sort_by": sort_by.value,
        "sort_dir": sort_dir.value,
        "filter": filter_mode.value,
    }


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
        # A successful password POST already issued a grant. Reuse it for the
        # canonical GET only after a fresh shared lifecycle/token/session fence;
        # rendering this path must never upgrade to session-admission locks.
        session_id = browser_cookie_session_id(request, gallery.id)
        if session_id is not None:
            try:
                gallery = await require_gallery_read_access(gallery, session_id, db, token=token)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_403_FORBIDDEN:
                    raise
            else:
                session = await current_share_session(gallery, session_id, db)
                if session is not None and (session.completed_at is None or gallery.status == GalleryStatus.completed):
                    return await _render_admitted_gallery(request, gallery, token, session, db)
            # A revoked/expired grant or a completed visit after reopening must
            # pass the password gate again before admission of a new visit.
            await db.commit()
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

    return await _render_admitted_gallery(request, gallery, token, session, db)


async def _render_admitted_gallery(
    request: Request, gallery: Gallery, token: str, session: ShareSession, db: AsyncSession
) -> HTMLResponse:
    """Render under an already-held access/admission fence without new writes."""
    cookie, csrf = browser_session(gallery.id, session.id, request=request)
    page = await load_guest_image_page(gallery, db)
    page_context = _page_context(page, ImageSortBy.sort_order, SortDirection.asc, ImageFilter.all)

    response = templates.TemplateResponse(
        request,
        "guest/viewer.html",
        {
            "request": request,
            "gallery_name": gallery.name,
            "gallery_message": gallery.guest_message,
            "token": token,
            "requires_password": False,
            **page_context,
            "guest_csrf_token": csrf,
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
    cursor: str | None = Query(None, max_length=MAX_CURSOR_LENGTH),
    offset: int = Query(0, ge=0, le=0),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """A bounded grid/lightbox page; cursor continuations append sibling tiles."""
    gallery = await resolve_gallery_by_token(token, db)
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    check_gallery_accessible(gallery)
    session_id = request_session_id(request, gallery.id)
    gallery = await require_gallery_read_access(gallery, session_id, db, token=token)

    page = await load_guest_image_page(gallery, db, sort_by, sort_dir, filter, cursor)
    page_context = _page_context(page, sort_by, sort_dir, filter, append_mode=cursor is not None)

    response = templates.TemplateResponse(
        request,
        "guest/_image_grid.html",
        {
            "request": request,
            **page_context,
            "token": token,
            "session_completed": gallery.status == GalleryStatus.completed,
        },
        headers=PRIVATE_HEADERS,
    )
    await db.commit()
    return response


@router.post("/{token}/verify-password", response_class=HTMLResponse)
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
    return cast(
        HTMLResponse,
        await _verify_resolved_password(request=request, gallery=gallery, token=token, db=db, password=password),
    )


@limiter.limit("5/minute")
async def _verify_resolved_password(
    request: Request, gallery: Gallery, token: str, db: AsyncSession, password: str
) -> HTMLResponse:
    return await _render_gallery_viewer(request, gallery, token, db, password=password)
