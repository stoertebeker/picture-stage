"""Tests for frontend auth: login, signup, verify-email, logout."""

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def test_login_route_exists():
    """GET /login route is defined in frontend auth router."""
    auth_py = (PROJECT_ROOT / "app" / "frontend" / "auth.py").read_text()
    assert '"/login"' in auth_py
    assert "login_page" in auth_py


def test_signup_route_exists():
    """GET /signup route is defined in frontend auth router."""
    auth_py = (PROJECT_ROOT / "app" / "frontend" / "auth.py").read_text()
    assert '"/signup"' in auth_py
    assert "signup_page" in auth_py


def test_logout_route_exists():
    """POST /logout route is defined in frontend auth router."""
    auth_py = (PROJECT_ROOT / "app" / "frontend" / "auth.py").read_text()
    assert '"/logout"' in auth_py
    assert "logout_page" in auth_py


def test_login_template_has_csrf():
    """Login template includes CSRF token hidden input."""
    login_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "login.html").read_text()
    assert 'name="csrf_token"' in login_html
    assert 'type="hidden"' in login_html


def test_login_template_has_dark_mode():
    """Login template uses dark: utility classes and the data-theme toggle widget.

    After ps-ux-04 the per-template Alpine `darkMode` variable was removed in
    favour of a global [data-theme] attribute managed by app.js. The visible
    contract is: tailwind dark: utilities are present, and the toggle button
    carries the data-theme-toggle attribute.
    """
    login_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "login.html").read_text()
    assert "dark:" in login_html
    assert "data-theme-toggle" in login_html


def test_signup_template_has_password_confirm():
    """Signup template has password confirmation field."""
    signup_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "signup.html").read_text()
    assert 'name="password_confirm"' in signup_html


def test_signup_template_has_csrf():
    """Signup template includes CSRF token hidden input."""
    signup_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "signup.html").read_text()
    assert 'name="csrf_token"' in signup_html
    assert 'type="hidden"' in signup_html


def test_signup_template_has_dark_mode():
    """Signup template has dark: classes for dark mode support."""
    signup_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "signup.html").read_text()
    assert "dark:" in signup_html


def test_verify_template_exists():
    """Verify email template exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "auth" / "verify.html").is_file()


def test_verify_template_has_dark_mode():
    """Verify template has dark: classes for dark mode support."""
    verify_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "verify.html").read_text()
    assert "dark:" in verify_html


def test_router_registered_in_main():
    """Frontend auth router is included in app/main.py."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "frontend_auth_router" in main_py or "frontend.auth" in main_py
