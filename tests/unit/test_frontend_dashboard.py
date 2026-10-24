"""Tests for frontend dashboard: gallery list with status and progress."""

import pathlib
import uuid
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.db.models import GalleryStatus
from app.frontend.deps import templates

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def test_dashboard_route_exists():
    """Check that dashboard.py defines GET /dashboard route."""
    dashboard_py = (PROJECT_ROOT / "app" / "frontend" / "dashboard.py").read_text()
    assert '"/dashboard"' in dashboard_py
    assert "async def dashboard" in dashboard_py


def test_dashboard_requires_auth():
    """Check that dashboard route uses require_authenticated_page."""
    dashboard_py = (PROJECT_ROOT / "app" / "frontend" / "dashboard.py").read_text()
    assert "require_authenticated_page" in dashboard_py


def test_dashboard_template_has_gallery_grid():
    """Check that dashboard template has responsive grid classes."""
    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert "grid-cols-1" in index_html
    assert "md:grid-cols-2" in index_html
    assert "xl:grid-cols-3" in index_html


def test_gallery_card_partial_exists():
    """Check that _gallery_card.html partial exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "dashboard" / "_gallery_card.html").is_file()


def test_dashboard_has_create_button():
    """Check that dashboard has hx-post for gallery creation."""
    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert 'hx-post="/dashboard/galleries"' in index_html


def test_gallery_card_has_status_badges():
    """Gallery card uses the status_pill macro (ps-ux-14). The macro knows all
    four status keys (draft, shared, completed, archived) plus expiry variants.
    We assert the card invokes the macro with the runtime status, and the
    macro itself enumerates the four statuses."""
    card_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "_gallery_card.html").read_text()
    assert "status_pill" in card_html
    assert "g.gallery.status.value" in card_html
    cards_macro = (PROJECT_ROOT / "app" / "templates" / "_macros" / "cards.html").read_text()
    for status in ("draft", "shared", "completed", "archived"):
        assert status in cards_macro


def test_gallery_card_has_quick_actions():
    """Check that gallery card has quick-action links."""
    card_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "_gallery_card.html").read_text()
    assert "/galleries/" in card_html
    assert "export" in card_html.lower()
    assert "#share-section" in card_html


def test_gallery_card_has_cover_image_surface():
    """Dashboard gallery cards use a hero cover surface."""
    card_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "_gallery_card.html").read_text()
    dashboard_py = (PROJECT_ROOT / "app" / "frontend" / "dashboard.py").read_text()
    assert "g.cover_url" in card_html
    assert "aspect-[5/3]" in card_html
    assert "cover_url" in dashboard_py
    assert "sign_url" in dashboard_py


def test_dashboard_has_empty_state():
    """Check that dashboard template has an empty state message (via i18n key)."""
    import json

    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert "t('dashboard.no_galleries_title')" in index_html
    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())
    assert de["dashboard"]["no_galleries_title"] == "Noch keine Galerien"


def test_dashboard_has_prominent_new_gallery_cta():
    """Dashboard keeps the HTMX create flow prominent."""
    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert "dashboard.metric_galleries" in index_html
    assert "dashboard.create_hint" in index_html
    assert 'hx-target="#gallery-grid"' in index_html
    assert 'hx-swap="beforeend"' in index_html


def test_dashboard_router_registered():
    """Check that the dashboard router is included in main.py."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "frontend_dashboard_router" in main_py
    assert "app.include_router(frontend_dashboard_router)" in main_py


def test_dashboard_extends_base():
    """Check that dashboard template extends base.html."""
    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert '{% extends "base.html" %}' in index_html


class _DashboardElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.forms = {}
        self.current_form = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "form":
            self.current_form = attributes.get("action")
            self.forms[self.current_form] = []
        elif tag == "input" and self.current_form is not None:
            self.forms[self.current_form].append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


@pytest.mark.parametrize(("locale", "label"), [("de", "Löschen"), ("en", "Delete")])
def test_dashboard_delete_dialogs_keep_gallery_identity_and_csrf(locale, label):
    """Multiple cards must target their own forms, including quoted/HTML names."""
    galleries = [
        SimpleNamespace(
            id=uuid.uuid4(),
            name=name,
            status=GalleryStatus.draft,
            share_token_hash=None,
            expires_at=None,
        )
        for name in ('O\'Brien "Shooting" <script>alert(1)</script> & mehr', "Andere Galerie")
    ]
    request = Request({"type": "http", "headers": []})
    request.state.locale = locale
    request.state.csrf_token = "current-session-csrf"
    response = templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "galleries": [
                dict(
                    gallery=gallery,
                    image_count=0,
                    selected_count=0,
                    favorited_count=0,
                    commented_count=0,
                    last_activity=None,
                    cover_url=None,
                )
                for gallery in galleries
            ]
        },
    )
    html = response.body.decode()
    parser = _DashboardElements()
    parser.feed(html)
    elements = parser.elements
    ids = [attrs["id"] for _, attrs in elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert not any(tag == "script" and "src" not in attrs for tag, attrs in elements)
    assert label in html
    for gallery in galleries:
        dialog_id = f"deleteModal-{gallery.id}"
        assert any(tag == "button" and attrs.get("data-open-dialog") == dialog_id for tag, attrs in elements)
        assert any(
            tag == "dialog" and attrs.get("id") == dialog_id and attrs.get("aria-labelledby") == f"{dialog_id}-title"
            for tag, attrs in elements
        )
        assert any(
            tag == "form" and attrs.get("method") == "post" and attrs.get("action") == f"/galleries/{gallery.id}/delete"
            for tag, attrs in elements
        )
        assert any(
            attrs.get("x-data") == "galleryDelete" and attrs.get("data-gallery-name") == gallery.name
            for _, attrs in elements
        )
        assert any(
            attrs.get("name") == "csrf_token" and attrs.get("value") == "current-session-csrf"
            for attrs in parser.forms[f"/galleries/{gallery.id}/delete"]
        )
