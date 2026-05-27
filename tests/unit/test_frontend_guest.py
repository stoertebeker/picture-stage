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
    """Guest viewer uses dark: Tailwind classes."""
    viewer_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "viewer.html").read_text()
    assert "dark:" in viewer_html


def test_guest_viewer_has_selection_toolbar():
    """Guest viewer has select/favorite buttons."""
    viewer_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "viewer.html").read_text()
    assert "toggleSelect" in viewer_html
    assert "toggleFavorite" in viewer_html


def test_guest_viewer_has_complete_button():
    """Guest viewer has a 'Bewertung abschließen' button."""
    viewer_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "viewer.html").read_text()
    assert "abschließen" in viewer_html.lower() or "abschliessen" in viewer_html.lower()


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
