"""Integration tests for proxy-agnostic cookie security (picture-stage-8ox).

The Secure flag and HSTS are driven by settings.cookie_secure (derived from
APP_URL), not the request scheme — so they must apply even though the in-process
test client speaks plain http://. We patch settings.app_url to flip the flag and
assert the raw Set-Cookie attributes / HSTS header.
"""

import pytest

from app.config import settings
from app.security.middleware import _mint_csrf_token


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
async def test_first_get_renders_the_same_csrf_token_that_it_sets(client, monkeypatch):
    """A fresh browser can submit the first rendered form without reloading."""
    monkeypatch.setattr(settings, "app_url", "http://localhost:8000")

    response = await client.get("/login")

    token = response.cookies.get("csrf_token")
    assert token
    assert f'name="csrf_token" value="{token}"' in response.text
    assert f'<meta name="csrf-token" content="{token}">' in response.text

    # The form from that first response is immediately usable. Invalid
    # credentials reach the login handler (401) instead of CSRF rejecting the
    # request before routing (403).
    submitted = await client.post(
        "/login",
        data={"csrf_token": token, "email": "missing@example.com", "password": "wrong"},
    )
    assert submitted.status_code == 401


@pytest.mark.asyncio
async def test_existing_csrf_cookie_is_rendered_without_rotation(client, monkeypatch):
    monkeypatch.setattr(settings, "app_url", "http://localhost:8000")
    client.cookies.set("csrf_token", "existing-token")

    response = await client.get("/login")

    assert _set_cookie_for(response, "csrf_token") is None
    assert 'name="csrf_token" value="existing-token"' in response.text
    assert '<meta name="csrf-token" content="existing-token">' in response.text


@pytest.mark.asyncio
async def test_authenticated_get_replaces_unbound_csrf_cookie(client, monkeypatch):
    monkeypatch.setattr(settings, "app_url", "http://localhost:8000")
    monkeypatch.setattr(settings, "secret_key", "csrf-test-secret")
    client.cookies.set("session", "opaque-session")
    client.cookies.set("csrf_token", "attacker-chosen")

    response = await client.get("/login")

    expected = _mint_csrf_token("opaque-session")
    assert response.cookies.get("csrf_token") == expected
    assert f'name="csrf_token" value="{expected}"' in response.text


@pytest.mark.asyncio
async def test_authenticated_post_rejects_matching_but_unsigned_csrf_cookie(client, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "csrf-test-secret")
    client.cookies.set("session", "opaque-session")
    client.cookies.set("csrf_token", "attacker-chosen")

    response = await client.post(
        "/login",
        data={
            "csrf_token": "attacker-chosen",
            "email": "missing@example.com",
            "password": "wrong",
        },
    )

    assert response.status_code == 403


def test_missing_csrf_cookie_mints_stable_token_for_parallel_session_gets(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "csrf-test-secret")

    first = _mint_csrf_token("same-session")
    second = _mint_csrf_token("same-session")

    assert first == second
    assert first != _mint_csrf_token("other-session")


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
