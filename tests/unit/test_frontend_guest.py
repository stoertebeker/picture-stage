"""Tests for frontend guest viewer: image grid, lightbox, selections, complete."""

import json
import pathlib
from html.parser import HTMLParser

from app.frontend.deps import templates
from app.i18n import t as translate

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _t(key: str, **kw: object) -> str:
    return translate(key, "de", **kw)


class _DataImagesExtractor(HTMLParser):
    """Capture the decoded value of the first ``data-images`` attribute.

    HTMLParser resolves HTML-entity/attribute encoding and respects the
    quote boundaries exactly as a browser would before handing the string to
    Alpine for ``JSON.parse`` — so a broken attribute surfaces as truncated or
    malformed JSON here too.
    """

    def __init__(self) -> None:
        super().__init__()
        self.value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.value is not None:
            return
        for name, val in attrs:
            if name == "data-images":
                self.value = val or ""
                return


def _make_image(filename: str, idx: int = 0) -> dict[str, object]:
    """Mirror the dict shape produced by app.frontend.guest._load_images."""
    return {
        "id": f"00000000-0000-0000-0000-{idx:012d}",
        "filename": filename,
        "sort_order": idx,
        "thumb_sm_url": "/media/x?sig=1",
        "thumb_md_url": "/media/x?sig=1",
        "preview_url": "/media/x?sig=1",
        "selected": False,
        "favorited": False,
        "comment": None,
    }


def _render_viewer(images: list[dict[str, object]]) -> str:
    return templates.env.get_template("guest/viewer.html").render(
        t=_t,
        locale="de",
        gallery_name="Test Gallery",
        token="tok123",
        session_id="sess123",
        images=images,
        total_images=len(images),
        selected_count=0,
        favorited_count=0,
        session_completed=False,
        requires_password=False,
        sort_by="sort_order",
        sort_dir="asc",
        filter="all",
    )


def _extract_data_images(html: str) -> str | None:
    parser = _DataImagesExtractor()
    parser.feed(html)
    return parser.value


def test_guest_viewer_data_images_is_parseable_json() -> None:
    """picture-stage-0kv (regression for picture-stage-2ba).

    The guestViewer root's data-images attribute must hold valid JSON that
    survives HTML-attribute encoding, so Alpine's JSON.parse yields the full
    image list instead of [] (the empty-array fallback that produced the black
    lightbox + '3/0' counter). No test caught the original break."""
    images = [_make_image("portrait.jpg", 0), _make_image("studio.png", 1)]
    raw = _extract_data_images(_render_viewer(images))

    assert raw is not None, "data-images attribute missing from rendered viewer"
    parsed = json.loads(raw)
    assert [img["filename"] for img in parsed] == ["portrait.jpg", "studio.png"]


def test_guest_viewer_data_images_survives_special_chars_in_filename() -> None:
    """Filenames with quotes, ampersands and angle brackets must not collide
    with the attribute quoting — that quote collision was the 2ba root cause.
    A browser-faithful parse must still recover every filename verbatim."""
    filenames = [
        'a"double".jpg',
        "o'single'.jpg",
        "a & b.jpg",
        "</script><img>.jpg",
    ]
    images = [_make_image(name, idx) for idx, name in enumerate(filenames)]
    raw = _extract_data_images(_render_viewer(images))

    assert raw is not None
    parsed = json.loads(raw)
    assert [img["filename"] for img in parsed] == filenames


def test_guest_viewer_data_images_does_not_break_out_of_markup() -> None:
    """Defense in depth: a filename carrying markup must never appear raw in the
    rendered HTML (it would let a malicious filename inject DOM). tojson escapes
    < > & ' to \\uXXXX, so the breakout substring stays encoded."""
    images = [_make_image("</script><img src=x onerror=alert(1)>.jpg", 0)]
    html = _render_viewer(images)

    assert "<img src=x onerror=alert(1)>" not in html
    # The data attribute still round-trips to the exact filename.
    parsed = json.loads(_extract_data_images(html) or "")
    assert parsed[0]["filename"] == "</script><img src=x onerror=alert(1)>.jpg"


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


def test_guest_image_grid_avoids_optional_chaining():
    """Grid Alpine expressions must not use optional chaining (picture-stage-2gb).

    The @alpinejs/csp build cannot parse `images[N]?.selected`; such expressions
    throw at runtime and silently kill grid reactivity (selection ring, check
    badge, hover toggles, favorite heart). The grid must call the null-safe
    isSelected()/isFavorited() helpers on guestViewer instead.
    """
    grid_html = (PROJECT_ROOT / "app" / "templates" / "guest" / "_image_grid.html").read_text()
    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()

    assert "?." not in grid_html, "optional chaining is not CSP-compatible"
    assert "isSelected(" in grid_html
    assert "isFavorited(" in grid_html
    assert "isSelected(idx)" in components_js
    assert "isFavorited(idx)" in components_js


def test_guest_router_registered():
    """Frontend guest router is included in main.py before API guest router."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "frontend_guest_router" in main_py
    guest_pos = main_py.index("include_router(frontend_guest_router)")
    api_guest_pos = main_py.index("include_router(guest_router)")
    assert guest_pos < api_guest_pos
