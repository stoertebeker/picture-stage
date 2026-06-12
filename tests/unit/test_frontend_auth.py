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
    """Login template includes the CSRF input — now via the csrf_input macro
    (ps-ux-11). The macro emits a hidden input with name="csrf_token", so the
    contract holds at render time. We assert here that the template invokes
    the macro."""
    login_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "login.html").read_text()
    assert "csrf_input" in login_html


def test_login_template_has_dark_mode():
    """Login supports theme switching via the shared auth_base layout (gmo).

    Editorial Dark replaced the legacy `dark:` Tailwind utilities with the
    [data-theme] token system, and gmo moved the page chrome into auth_base.
    The contract is now: the page extends auth_base, which carries the
    data-theme-toggle widget (managed globally by app.js).
    """
    login_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "login.html").read_text()
    auth_base = (PROJECT_ROOT / "app" / "templates" / "auth_base.html").read_text()
    assert 'extends "auth_base.html"' in login_html
    assert "data-theme-toggle" in auth_base


def test_signup_template_has_password_confirm():
    """Signup template has password confirmation field — now via the
    password_input macro (ps-ux-11) with name='password_confirm'."""
    signup_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "signup.html").read_text()
    assert "password_confirm" in signup_html


def test_signup_template_has_csrf():
    """Signup template includes the CSRF input — now via the csrf_input macro
    (ps-ux-11)."""
    signup_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "signup.html").read_text()
    assert "csrf_input" in signup_html


def test_signup_template_has_dark_mode():
    """Signup supports theme switching via the shared auth_base layout (gmo).

    See test_login_template_has_dark_mode — the [data-theme] toggle lives in
    auth_base now, not in per-template `dark:` utilities."""
    signup_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "signup.html").read_text()
    auth_base = (PROJECT_ROOT / "app" / "templates" / "auth_base.html").read_text()
    assert 'extends "auth_base.html"' in signup_html
    assert "data-theme-toggle" in auth_base


def test_verify_template_exists():
    """Verify email template exists."""
    assert (PROJECT_ROOT / "app" / "templates" / "auth" / "verify.html").is_file()


def test_verify_template_has_dark_mode():
    """Verify supports theme switching via the shared auth_base layout (gmo).

    See test_login_template_has_dark_mode — the [data-theme] toggle lives in
    auth_base now, not in per-template `dark:` utilities."""
    verify_html = (PROJECT_ROOT / "app" / "templates" / "auth" / "verify.html").read_text()
    auth_base = (PROJECT_ROOT / "app" / "templates" / "auth_base.html").read_text()
    assert 'extends "auth_base.html"' in verify_html
    assert "data-theme-toggle" in auth_base


def test_router_registered_in_main():
    """Frontend auth router is included in app/main.py."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "frontend_auth_router" in main_py or "frontend.auth" in main_py


def test_signup_has_no_409_enumeration_leak():
    """picture-stage-42q: neither signup path returns a 409 / existence hint.

    An already-registered email must yield the same neutral response as a fresh
    signup. The old i18n leak keys must be gone from both code paths.
    """
    frontend = (PROJECT_ROOT / "app" / "frontend" / "auth.py").read_text()
    api = (PROJECT_ROOT / "app" / "auth" / "router.py").read_text()

    # No existence-revealing status codes / messages in the signup flow.
    assert "409" not in frontend
    assert "auth.email_registered" not in frontend
    assert "auth.signup_pending" not in frontend
    assert "Email already registered" not in api
    assert "Signup already pending" not in api

    # The removed i18n keys are gone from both locale files.
    for locale in ("de.json", "en.json"):
        text = (PROJECT_ROOT / "app" / "i18n" / locale).read_text()
        assert "email_registered" not in text
        assert '"signup_pending":' not in text  # the auth.* leak key; the notification event keeps its own


def test_signup_response_does_not_expose_verification_token():
    """picture-stage-42q: token-vs-null in the response would itself leak existence."""
    from app.auth.schemas import SignupResponse

    # SignupResponse must no longer declare the verification_token field
    # (an explanatory comment mentioning the name is fine — check the model).
    assert "verification_token" not in SignupResponse.model_fields
