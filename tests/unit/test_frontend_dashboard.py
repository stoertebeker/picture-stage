"""Tests for frontend dashboard: gallery list with status and progress."""

import pathlib

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
    assert "lg:grid-cols-3" in index_html


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


def test_dashboard_has_empty_state():
    """Check that dashboard template has an empty state message (via i18n key)."""
    import json

    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert "t('dashboard.no_galleries_title')" in index_html
    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())
    assert de["dashboard"]["no_galleries_title"] == "Noch keine Galerien"


def test_dashboard_router_registered():
    """Check that the dashboard router is included in main.py."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "frontend_dashboard_router" in main_py
    assert "app.include_router(frontend_dashboard_router)" in main_py


def test_dashboard_extends_base():
    """Check that dashboard template extends base.html."""
    index_html = (PROJECT_ROOT / "app" / "templates" / "dashboard" / "index.html").read_text()
    assert '{% extends "base.html" %}' in index_html
