"""Tests for frontend foundation: static files, templates, cookie auth, CSRF, dark mode."""

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def test_static_files_mount_exists():
    """Check that StaticFiles mount is configured in app/main.py."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "StaticFiles" in main_py
    assert 'directory="frontend/static"' in main_py


def test_templates_directory_exists():
    """Check that app/templates/base.html exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "base.html").is_file()


def test_base_template_has_dark_mode():
    """Check that base.html uses dark: classes for dark mode."""
    base_html = (PROJECT_ROOT / "app" / "templates" / "base.html").read_text()
    assert "dark:" in base_html


def test_base_template_includes_htmx():
    """Check that base.html includes htmx.min.js script tag."""
    base_html = (PROJECT_ROOT / "app" / "templates" / "base.html").read_text()
    assert "htmx.min.js" in base_html


def test_base_template_includes_alpine():
    """Check that base.html includes alpine.min.js script tag."""
    base_html = (PROJECT_ROOT / "app" / "templates" / "base.html").read_text()
    assert "alpine.min.js" in base_html


def test_cookie_auth_dependency_exists():
    """Check that get_user_from_cookie is defined in dependencies.py."""
    deps_py = (PROJECT_ROOT / "app" / "auth" / "dependencies.py").read_text()
    assert "get_user_from_cookie" in deps_py


def test_csrf_in_middleware():
    """Check that CSRF handling is present in middleware.py."""
    middleware_py = (PROJECT_ROOT / "app" / "security" / "middleware.py").read_text()
    assert "csrf" in middleware_py.lower()


def test_guest_base_template_exists():
    """Check that guest_base.html exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "guest_base.html").is_file()


def test_app_js_has_dark_mode():
    """app.js owns the theme bootstrap + toggle.

    After ps-ux-04 the function was renamed `toggleTheme` and operates on a
    [data-theme] attribute on <html>. The IIFE at the top sets the initial
    value (default = 'dark') before any pixel paints.
    """
    app_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "app.js").read_text()
    assert "toggleTheme" in app_js
    assert "data-theme" in app_js


def test_tailwind_config_exists():
    """Check that tailwind.config.js exists at project root."""
    assert (PROJECT_ROOT / "tailwind.config.js").is_file()


def test_skip_link_on_all_standalone_pages():
    """WCAG 2.4.1 (p07.8): every template with its own <body> ships a skip
    link to #main as first focusable element, and a matching id="main"
    landmark. Covers base/guest_base (inherited by all pages) plus the
    standalone auth/setup heads."""
    pages = [
        ("base.html",),
        ("guest_base.html",),
        ("auth", "login.html"),
        ("auth", "signup.html"),
        ("auth", "verify.html"),
        ("setup", "index.html"),
    ]
    for parts in pages:
        html = (PROJECT_ROOT / "app" / "templates").joinpath(*parts).read_text()
        assert 'href="#main"' in html, f"skip link missing in {parts}"
        assert "skip_to_content" in html, f"skip link not i18n'd in {parts}"
        assert 'id="main"' in html, f"#main target missing in {parts}"
        # Skip link must come before the target so it is the first tab stop.
        assert html.index('href="#main"') < html.index('id="main"'), parts


def test_skip_to_content_i18n_key_exists():
    """The skip-link label exists in both locales."""
    import json

    for locale in ("de", "en"):
        data = json.loads((PROJECT_ROOT / "app" / "i18n" / f"{locale}.json").read_text())
        assert data["common"]["skip_to_content"]


def test_nav_settings_dropdown():
    """dxj: top nav has brand left and a settings dropdown that owns the
    theme toggle and the language switcher (CSP-safe Alpine component)."""
    base_html = (PROJECT_ROOT / "app" / "templates" / "base.html").read_text()
    nav = base_html[base_html.index("<nav") : base_html.index("</nav>")]
    # Brand stays first, settings dropdown wraps toggle + language switcher.
    assert nav.index("Picture-Stage") < nav.index('x-data="settingsMenu"')
    dropdown = nav[nav.index('x-data="settingsMenu"') :]
    assert "nav.settings" in dropdown
    assert "data-theme-toggle" in dropdown
    assert 'x-data="langSwitcher"' in dropdown
    # A11y: expanded state bound + safe initial value, ESC and outside close.
    assert ':aria-expanded="expanded"' in dropdown
    assert 'aria-expanded="false"' in dropdown
    assert "@keydown.escape.window" in dropdown
    assert "@click.outside" in dropdown

    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()
    assert "settingsMenuComponent" in components_js
    assert "Alpine.data('settingsMenu', settingsMenuComponent)" in components_js


def test_nav_settings_i18n_key_exists():
    """nav.settings exists in both locales."""
    import json

    for locale in ("de", "en"):
        data = json.loads((PROJECT_ROOT / "app" / "i18n" / f"{locale}.json").read_text())
        assert data["nav"]["settings"]


def test_guest_pages_have_theme_toggle():
    """dd1: guest viewer + guest_base (expired page) ship a theme toggle
    button using the generic [data-theme-toggle] wiring from app.js, with
    the p07.1 ARIA pattern and both theme-label icon spans."""
    for parts in (("guest", "viewer.html"), ("guest_base.html",)):
        html = (PROJECT_ROOT / "app" / "templates").joinpath(*parts).read_text()
        assert "data-theme-toggle" in html, f"theme toggle missing in {parts}"
        toggle = html[html.index("data-theme-toggle") :]
        assert 'aria-pressed="true"' in toggle, parts
        assert "nav.toggle_theme" in html, parts
        assert 'data-theme-label="light"' in toggle, parts
        assert 'data-theme-label="dark"' in toggle, parts
