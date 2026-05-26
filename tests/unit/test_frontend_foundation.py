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
    """Check that app.js has toggleDarkMode function."""
    app_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "app.js").read_text()
    assert "toggleDarkMode" in app_js


def test_tailwind_config_exists():
    """Check that tailwind.config.js exists at project root."""
    assert (PROJECT_ROOT / "tailwind.config.js").is_file()
