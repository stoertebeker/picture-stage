"""Tests for frontend guest viewer: image grid, lightbox, selections, complete."""

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def test_guest_route_returns_html():
    """GET /g/{token} route is defined in frontend guest router."""
    guest_py = (PROJECT_ROOT / "app" / "frontend" / "guest.py").read_text()
    assert '"/{token}"' in guest_py
    assert "guest_viewer" in guest_py
    assert "HTMLResponse" in guest_py


def test_guest_viewer_has_alpine_state():
    """Guest viewer template has Alpine.js x-data state."""
    viewer_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "viewer.html").read_text()
    assert "x-data" in viewer_html
    assert "guestViewer()" in viewer_html


def test_guest_viewer_has_lightbox():
    """Lightbox partial template exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "guest" / "_lightbox.html").is_file()
    lightbox_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "_lightbox.html").read_text()
    assert "lightboxOpen" in lightbox_html
    assert "ArrowRight" in lightbox_html or "nextImage" in lightbox_html


def test_guest_viewer_has_dark_mode():
    """Guest viewer renders under data-theme='dark' and uses Editorial-Dark
    semantic tokens (bg-surface-*, text-text-*). After ps-ux-20b the legacy
    `dark:bg-gray-*` utility duplicates were removed in favour of the new
    token classes."""
    viewer_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "viewer.html").read_text()
    assert "bg-surface-base" in viewer_html
    assert "text-text-primary" in viewer_html


def test_guest_viewer_has_selection_toolbar():
    """Guest viewer has select/favorite buttons wired to the guestViewer component.

    After the inline-script extraction (ps-ux-02 hardening), the component
    methods live in frontend/static/js/components.js while the buttons that
    call them live in the image-grid / lightbox partials.
    """
    image_grid = (PROJECT_ROOT / "app" / "templates" / "guest" / "_image_grid.html").read_text()
    lightbox = (PROJECT_ROOT / "app" / "templates" / "guest" / "_lightbox.html").read_text()
    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()

    assert "toggleSelect" in image_grid
    assert "toggleFavorite" in image_grid
    assert "toggleSelect" in lightbox
    assert "toggleFavorite" in lightbox
    assert "toggleSelect" in components_js
    assert "toggleFavorite" in components_js


def test_guest_image_grid_does_not_nest_action_buttons_inside_lightbox_button():
    """Selection/favorite controls must be siblings of the lightbox opener.

    Nested buttons are invalid HTML and can make click handling browser
    dependent, especially for the overlaid guest selection controls.
    """
    grid_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "_image_grid.html").read_text()
    opener_start = grid_html.index('@click="openLightbox')
    opener_end = grid_html.index("</button>", opener_start)
    opener_block = grid_html[opener_start:opener_end]

    assert "toggleSelect" not in opener_block
    assert "toggleFavorite" not in opener_block
    assert "z-10" in grid_html


def test_guest_selection_api_requires_explicit_session_id():
    """Guest selection events must bind to the caller's share session."""
    schema_py = (PROJECT_ROOT / "app" / "selections" / "schemas.py").read_text()
    router_py = (PROJECT_ROOT / "app" / "guest" / "router.py").read_text()
    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()

    assert "session_id: uuid.UUID" in schema_py
    assert "ShareSession.id == body.session_id" in router_py
    assert "ShareSession.gallery_id == gallery.id" in router_py
    assert "ShareSession.completed_at.is_(None)" in router_py
    assert "session_id: this.sessionId" in components_js


def test_guest_viewer_has_complete_button():
    """Guest viewer offers a complete-selection action (via i18n key).

    The redesigned viewer (ps-ux-20b) uses the floating-pill phrasing
    'Auswahl abschließen' (guest.complete_pill), while the modal still
    confirms with the longer guest.complete_button. Both keys must say
    'abschließen' in DE."""
    import json

    viewer_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "viewer.html").read_text()
    assert "t('guest.complete_pill')" in viewer_html
    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())
    assert "abschließen" in de["guest"]["complete_pill"].lower()
    assert "abschließen" in de["guest"]["complete_button"].lower()


def test_guest_password_template_exists():
    """Password prompt partial exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "guest" / "_password.html").is_file()


def test_guest_complete_modal_exists():
    """Complete modal partial exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "guest" / "_complete_modal.html").is_file()


def test_guest_image_grid_exists():
    """Image grid partial exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "guest" / "_image_grid.html").is_file()
    grid_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "_image_grid.html").read_text()
    assert "grid" in grid_html


def test_guest_router_registered():
    """Frontend guest router is included in main.py before API guest router."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "frontend_guest_router" in main_py
    guest_pos = main_py.index("include_router(frontend_guest_router)")
    api_guest_pos = main_py.index("include_router(guest_router)")
    assert guest_pos < api_guest_pos
