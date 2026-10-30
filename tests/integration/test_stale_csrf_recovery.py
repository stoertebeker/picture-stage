"""Stale HTMX documents recover by GET without weakening signed CSRF (d18f)."""

import pytest

from app.security.middleware import _mint_csrf_token


@pytest.mark.parametrize("failure", ["missing_cookie", "unsigned_cookie", "stale_header", "missing_submission"])
async def test_htmx_csrf_failures_keep_403_and_request_safe_get_navigation(client, failure):
    client.cookies.set("session", "opaque-session")
    token = _mint_csrf_token("opaque-session")
    headers = {"HX-Request": "true"}
    if failure != "missing_cookie":
        client.cookies.set("csrf_token", "legacy-unsigned" if failure == "unsigned_cookie" else token)
    if failure != "missing_submission":
        headers["X-CSRF-Token"] = (
            token
            if failure == "missing_cookie"
            else ("legacy-unsigned" if failure in {"unsigned_cookie", "stale_header"} else token)
        )
    response = await client.post("/login", data={"email": "missing@example.com", "password": "wrong"}, headers=headers)
    assert response.status_code == 403
    assert response.headers["HX-Redirect"] == "/login"
    assert "HX-Refresh" not in response.headers
    assert response.text.startswith("CSRF token")
    assert "location" not in response.headers


async def test_native_csrf_failure_stays_rejected_without_htmx_refresh(client):
    client.cookies.set("session", "opaque-session")
    client.cookies.set("csrf_token", "legacy-unsigned")
    response = await client.post(
        "/login", data={"csrf_token": "legacy-unsigned", "email": "missing@example.com", "password": "wrong"}
    )
    assert response.status_code == 403
    assert "HX-Redirect" not in response.headers


async def test_safe_refresh_rotates_cookie_and_correct_submission_reaches_handler(client):
    client.cookies.set("session", "opaque-session")
    client.cookies.set("csrf_token", "legacy-unsigned")
    rejected = await client.post(
        "/login",
        data={"email": "missing@example.com", "password": "wrong"},
        headers={"HX-Request": "true", "X-CSRF-Token": "legacy-unsigned"},
    )
    assert rejected.status_code == 403
    refreshed = await client.get("/login")
    token = _mint_csrf_token("opaque-session")
    assert refreshed.cookies["csrf_token"] == token
    assert f'name="csrf_token" value="{token}"' in refreshed.text
    accepted_csrf = await client.post(
        "/login",
        data={"email": "missing@example.com", "password": "wrong"},
        headers={"HX-Request": "true", "X-CSRF-Token": token},
    )
    # Invalid credentials reach the handler, instead of the CSRF rejection path.
    assert accepted_csrf.status_code == 401
    assert "HX-Redirect" not in accepted_csrf.headers


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/galleries/12345678-1234-5678-1234-567812345678/upload", "/galleries/12345678-1234-5678-1234-567812345678"),
        ("/galleries/12345678-1234-5678-1234-567812345678/expiry", "/galleries/12345678-1234-5678-1234-567812345678"),
        ("/galleries/not-a-uuid/expiry", "/dashboard"),
        ("/admin/users", "/dashboard"),
        ("/signup", "/signup"),
    ],
)
async def test_csrf_recovery_uses_only_canonical_get_paths(client, path, target):
    rejected = await client.post(path, headers={"HX-Request": "true", "Referer": "https://attacker.invalid/"})
    assert rejected.status_code == 403
    assert rejected.headers["HX-Redirect"] == target
    assert "HX-Refresh" not in rejected.headers


@pytest.mark.parametrize("method", ["get", "post"])
async def test_session_html_pages_and_error_renders_require_private_revalidation(client, method):
    client.cookies.set("session", "opaque-session")
    token = _mint_csrf_token("opaque-session")
    client.cookies.set("csrf_token", token)
    if method == "get":
        response = await client.get("/login")
    else:
        response = await client.post(
            "/login", data={"email": "missing@example.com", "password": "wrong", "csrf_token": token}
        )
    assert response.headers["content-type"].startswith("text/html")
    assert {"private", "no-cache"} <= set(response.headers["cache-control"].replace(" ", "").split(","))


async def test_unauthenticated_html_does_not_gain_private_session_cache_policy(client):
    response = await client.get("/login")
    assert "private" not in response.headers.get("cache-control", "")
