"""Unit tests for the cookie_secure setting (picture-stage-8ox).

The Secure cookie flag and HSTS are derived from the operator-configured public
APP_URL rather than the (proxy-stripped/spoofable) request scheme. These tests
pin that derivation. ENVIRONMENT=development is passed so the production
secret-guard validator does not trip on the default test secrets.
"""

from app.config import Settings


def test_cookie_secure_true_for_https_app_url() -> None:
    assert Settings(app_url="https://photos.example.com", environment="development").cookie_secure is True


def test_cookie_secure_false_for_http_app_url() -> None:
    assert Settings(app_url="http://localhost:8000", environment="development").cookie_secure is False


def test_cookie_secure_is_case_insensitive() -> None:
    assert Settings(app_url="HTTPS://Photos.Example.COM", environment="development").cookie_secure is True


def test_cookie_secure_false_for_http_even_in_production_app_url() -> None:
    # The flag tracks the public scheme, not the environment label: a plain-HTTP
    # public URL must not get Secure cookies (they would never be sent).
    assert Settings(app_url="http://photos.example.com", environment="production").cookie_secure is False
