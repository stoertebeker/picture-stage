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
    # Alpine.data() registration → bare component name, no parentheses (u3s.1).
    assert 'x-data="guestViewer"' in viewer_html


def test_guest_viewer_has_lightbox():
    """Lightbox partial template exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "guest" / "_lightbox.html").is_file()
    lightbox_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "_lightbox.html").read_text()
    assert "lightboxOpen" in lightbox_html
    assert "ArrowRight" in lightbox_html or "nextImage" in lightbox_html


def test_guest_lightbox_is_token_based_and_accessible():
    """ps-ux-21b: the redesigned lightbox uses Editorial-Dark semantic tokens
    (so it follows data-theme for dark AND light), exposes aria-labels on
    icon-only controls, and preserves the read-only completed gate."""
    lightbox_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "_lightbox.html").read_text()

    # Token-based, theme-aware — no hard-coded raw colors that break light mode.
    assert "bg-accent" in lightbox_html
    assert "text-on-accent" in lightbox_html
    assert "text-favorite" in lightbox_html or "bg-favorite" in lightbox_html
    assert "bg-surface-overlay" in lightbox_html
    assert "bg-blue-500" not in lightbox_html
    assert "bg-yellow-500" not in lightbox_html
    assert "text-white" not in lightbox_html

    # Accessibility: icon-only controls are labelled, focus is visible.
    assert "guest.lightbox_close" in lightbox_html
    assert "guest.lightbox_prev" in lightbox_html
    assert "guest.lightbox_next" in lightbox_html
    assert "focus-visible:ring" in lightbox_html

    # Read-only gate from the persistence feature must survive the redesign.
    assert ':disabled="completed"' in lightbox_html


def test_guest_lightbox_i18n_keys_exist():
    """All i18n keys referenced by the lightbox exist in DE and EN."""
    import json

    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())["guest"]
    en = json.loads((PROJECT_ROOT / "app" / "i18n" / "en.json").read_text())["guest"]
    for key in ("lightbox_close", "lightbox_prev", "lightbox_next", "lightbox_swipe_hint"):
        assert key in de, f"DE missing guest.{key}"
        assert key in en, f"EN missing guest.{key}"


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
    """Guest selection events tag the caller's share session for audit.

    Selections are materialized gallery-wide (magic-link = one model), so the
    session is used for event attribution, not isolation. Read-only is enforced
    gallery-wide via the completed gallery status, not per session.
    """
    schema_py = (PROJECT_ROOT / "app" / "selections" / "schemas.py").read_text()
    router_py = (PROJECT_ROOT / "app" / "guest" / "router.py").read_text()
    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()

    assert "session_id: uuid.UUID" in schema_py
    assert "ShareSession.id == body.session_id" in router_py
    assert "ShareSession.gallery_id == gallery.id" in router_py
    # Read-only is now gallery-wide, not per-session.
    assert "gallery.status == GalleryStatus.completed" in router_py
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


def test_guest_password_gate_matches_editorial_mockup():
    """qdz.16: the gate follows the approved spike (guest_password.html).

    Editorial-Dark tokens only (theme-safe in dark AND light), gallery identity
    above the card, status-danger error alert, accessible form controls."""
    gate = (PROJECT_ROOT / "app" / "templates" / "guest" / "_password.html").read_text()

    # Gallery identity + trust mark per mockup.
    assert "guest.gallery_eyebrow" in gate
    assert "{{ gallery_name }}" in gate
    assert "font-display" in gate

    # Token-based styling — no legacy gray/blue utilities left.
    assert "bg-surface-raised" in gate
    assert "border-border-subtle" in gate
    assert "bg-accent" in gate
    assert "text-text-on-accent" in gate
    assert "dark:bg-gray-800" not in gate
    assert "bg-blue-600" not in gate

    # Error state: theme-safe status-danger alert, announced to AT.
    assert 'role="alert"' in gate
    assert "border-status-danger/40" in gate
    assert "bg-status-danger/10" in gate
    assert "text-status-danger" in gate

    # A11y: sr-only label tied to the input, visible focus.
    assert 'class="sr-only" for="gallery-password"' in gate
    assert 'id="gallery-password"' in gate
    assert "focus-visible:ring" in gate

    # Trust line below the card.
    assert "guest.password_privacy_note" in gate


def test_guest_password_gate_uses_plain_form_post():
    """qdz.16: the gate submits as a regular form POST (no HTMX grid swap).

    The old hx-post swap left the unlocked page without header, counters,
    lightbox and Alpine images state. Success/error now render full pages."""
    gate = (PROJECT_ROOT / "app" / "templates" / "guest" / "_password.html").read_text()
    guest_py = (PROJECT_ROOT / "app" / "frontend" / "guest.py").read_text()

    assert "hx-post" not in gate
    assert 'action="/g/{{ token }}/verify-password"' in gate
    # Both outcomes render the full viewer template, never the bare grid.
    assert "_render_gallery_viewer" in guest_py
    assert "_render_password_gate" in guest_py
    assert 'error_key="guest.password_error"' in guest_py
    assert "HTTP_401_UNAUTHORIZED" in guest_py


def test_guest_verify_password_is_rate_limited():
    """a2d: the HTML verify-password endpoint must be rate-limited like its
    JSON-API counterpart (5/minute), or the bcrypt gallery password can be
    brute-forced through the web form."""
    guest_py = (PROJECT_ROOT / "app" / "frontend" / "guest.py").read_text()

    handler_pos = guest_py.index("async def guest_verify_password")
    decorator_block = guest_py[:handler_pos].rsplit("@router.post", 1)[1]
    assert '@limiter.limit("5/minute")' in decorator_block


def test_guest_password_i18n_keys_exist():
    """All i18n keys referenced by the gate exist in DE and EN."""
    import json

    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())["guest"]
    en = json.loads((PROJECT_ROOT / "app" / "i18n" / "en.json").read_text())["guest"]
    for key in (
        "password_text",
        "password_placeholder",
        "password_submit",
        "password_error",
        "password_privacy_note",
        "gallery_eyebrow",
    ):
        assert key in de, f"DE missing guest.{key}"
        assert key in en, f"EN missing guest.{key}"
    # Friendly, full-sentence error per mockup (not the old curt 'Falsches Passwort').
    assert de["password_error"] != "Falsches Passwort"


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
