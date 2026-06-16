"""Integration tests for proxy-agnostic cookie security (picture-stage-8ox).

The Secure flag and HSTS are driven by settings.cookie_secure (derived from
APP_URL), not the request scheme — so they must apply even though the in-process
test client speaks plain http://. We patch settings.app_url to flip the flag and
assert the raw Set-Cookie attributes / HSTS header.
"""

import pytest

from app.config import settings


def _set_cookie_for(response, name: str) -> str | None:
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw
    return None


@pytest.mark.asyncio
async def test_csrf_cookie_secure_and_hsts_when_app_url_https(client, monkeypatch):
    monkeypatch.setattr(settings, "app_url", "https://photos.example.com")
    resp = await client.get("/login")

    csrf = _set_cookie_for(resp, "csrf_token")
    assert csrf is not None, "GET should set the csrf_token cookie"
    assert "Secure" in csrf
    # CSRF token stays JS-readable (double-submit), so it must NOT be HttpOnly.
    assert "HttpOnly" not in csrf
    assert resp.headers.get("strict-transport-security") is not None


@pytest.mark.asyncio
async def test_csrf_cookie_not_secure_and_no_hsts_when_app_url_http(client, monkeypatch):
    monkeypatch.setattr(settings, "app_url", "http://localhost:8000")
    resp = await client.get("/login")

    csrf = _set_cookie_for(resp, "csrf_token")
    assert csrf is not None
    assert "Secure" not in csrf
    assert resp.headers.get("strict-transport-security") is None


@pytest.mark.asyncio
async def test_api_logout_deletes_session_with_matching_attributes(client, monkeypatch):
    # /api/ is CSRF-exempt, so the logout POST goes through without a token.
    monkeypatch.setattr(settings, "app_url", "https://photos.example.com")
    resp = await client.post("/api/v1/auth/logout")

    session = _set_cookie_for(resp, "session")
    assert session is not None, "logout must emit a session deletion cookie"
    lowered = session.lower()
    assert "secure" in lowered
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    # Deletion = expired cookie (zero max-age / epoch expires).
    assert "max-age=0" in lowered or "expires=" in lowered
