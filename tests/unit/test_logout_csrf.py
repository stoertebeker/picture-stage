"""Real logout routes share CSRF protection, including router-equivalent aliases."""

import uuid
from html.parser import HTMLParser
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.passwords import hash_password
from app.auth.tokens import create_access_token
from app.db.models import User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates
from app.main import app
from app.security.middleware import _mint_csrf_token

LOGOUT_TARGETS = [
    ("", "/logout"),
    ("", "/logout/"),
    ("", "/logout///"),
    ("", "/%6cogout"),
    ("", "/logout%2F%2F%2F"),
    ("", "/api/v1/auth/logout"),
    ("", "/api/v1/auth/logout/"),
    ("", "/api/v1/auth/logout///"),
    ("", "/api/v1/auth/%6cogout"),
    ("", "/api%2Fv1%2Fauth%2Flogout%2F%2F%2F"),
    ("/photos", "/photos/api/v1/auth/logout"),
    ("/photos", "/photos/api/v1/auth/logout///"),
    ("/api", "/api/api/v1/auth/logout"),
    ("/api", "/api/api/v1/auth/logout///"),
    ("/api", "/api/logout"),
    ("/api", "/api/logout///"),
    # A similarly spelled root is not a path segment prefix of /api/.
    ("/ap", "/api/v1/auth/logout"),
]


def _bound_cookies():
    token = _mint_csrf_token("opaque-session")
    return token, {"Cookie": f"session=opaque-session; csrf_token={token}"}


@pytest.mark.parametrize(("root_path", "path"), LOGOUT_TARGETS)
@pytest.mark.parametrize(
    "proof", ["absent", "bearer-only", "no-cookie", "cookie-only", "wrong-header", "unsigned-cookie"]
)
async def test_logout_rejects_without_bound_double_submit_before_any_cookie_effect(root_path, path, proof):
    token, headers = _bound_cookies()
    body = ""
    if proof == "absent":
        headers = {}
    elif proof == "bearer-only":
        headers = {"Authorization": "Bearer attacker-controlled"}
    elif proof == "no-cookie":
        headers = {"X-CSRF-Token": token}
    elif proof == "wrong-header":
        headers.update({"X-CSRF-Token": "wrong", "Content-Type": "application/x-www-form-urlencoded"})
        body = urlencode({"csrf_token": token})
    elif proof == "unsigned-cookie":
        headers = {"Cookie": "session=opaque-session; csrf_token=unsigned", "X-CSRF-Token": "unsigned"}

    async with AsyncClient(transport=ASGITransport(app=app, root_path=root_path), base_url="http://test") as client:
        response = await client.post(path, headers=headers, content=body)

    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert "location" not in response.headers


@pytest.mark.parametrize(("root_path", "path"), LOGOUT_TARGETS)
@pytest.mark.parametrize("proof", ["header", "form"])
async def test_bound_logout_accepts_proof_and_preserves_router_slash_redirect(root_path, path, proof):
    token, headers = _bound_cookies()
    kwargs = {"headers": headers}
    if proof == "header":
        headers["X-CSRF-Token"] = token
    else:
        kwargs["data"] = {"csrf_token": token}

    async with AsyncClient(transport=ASGITransport(app=app, root_path=root_path), base_url="http://test") as client:
        response = await client.post(path, **kwargs)
        if response.request.url.path.endswith("/"):
            assert response.status_code == 307
            assert "set-cookie" not in response.headers
            response = await client.post(response.headers["location"], **kwargs)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    deletion = response.headers.get_list("set-cookie")
    assert len(deletion) == 1
    assert deletion[0].startswith('session="";')
    assert "Max-Age=0" in deletion[0]


@pytest.mark.parametrize("path", ["/logout", "/api/v1/auth/logout"])
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE"])
async def test_safe_methods_never_delete_the_session(path, method):
    _, headers = _bound_cookies()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, headers=headers)
    assert response.status_code == 405
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path", ["/logout", "/api/v1/auth/logout"])
@pytest.mark.parametrize(
    ("content_type", "header", "form", "expected"),
    [
        ("application/x-www-form-urlencoded; charset=UTF-8", None, "csrf_token={token}", 303),
        ("application/x-www-form-urlencoded", "", "csrf_token={token}", 303),
        ("application/x-www-form-urlencoded", "wrong", "csrf_token={token}", 403),
        ("application/x-www-form-urlencoded", "{token}", "csrf_token=wrong", 303),
        ("application/x-www-form-urlencoded", None, "csrf_token=wrong&csrf_token={token}", 403),
        ("application/x-www-form-urlencoded", None, "csrf_token={token}&csrf_token=wrong", 303),
        ("application/json", None, '{{"csrf_token":"{token}"}}', 403),
        ("application/json", "{token}", "{{}}", 303),
        ("multipart/form-data; boundary=example", "{token}", "", 303),
        ("multipart/form-data; boundary=example", None, "csrf_token={token}", 403),
    ],
)
async def test_logout_keeps_existing_header_and_form_parser_contract(path, content_type, header, form, expected):
    token, headers = _bound_cookies()
    headers["Content-Type"] = content_type
    if header is not None:
        headers["X-CSRF-Token"] = header.format(token=token)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, headers=headers, content=form.format(token=token))
    assert response.status_code == expected
    assert ("set-cookie" in response.headers) is (expected == 303)


@pytest.mark.parametrize("path", ["/logout", "/api/v1/auth/logout"])
async def test_logout_without_session_retains_existing_double_submit_contract(path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, headers={"Cookie": "csrf_token=proof", "X-CSRF-Token": "proof"})
    assert response.status_code == 303
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize("root_path", ["", "/api"])
async def test_json_login_and_bearer_locale_remain_csrf_exempt(monkeypatch, root_path):
    user = User(
        id=uuid.uuid4(),
        email="csrf-control@example.com",
        password_hash=hash_password("valid-password"),
        status=UserStatus.active,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    database = AsyncMock(spec=AsyncSession)
    database.execute.return_value = result
    database.scalar.return_value = None  # No logout revocation for the bearer control.

    async def fake_db():
        yield database

    monkeypatch.setitem(app.dependency_overrides, get_db, fake_db)
    async with AsyncClient(transport=ASGITransport(app=app, root_path=root_path), base_url="http://test") as client:
        login = await client.post(
            root_path + "/api/v1/auth/login", json={"email": user.email, "password": "valid-password"}
        )
        assert login.status_code == 200
        assert login.json()["access_token"]
        assert "set-cookie" not in login.headers

        locale = await client.put(
            root_path + "/api/v1/auth/locale",
            json={"locale": "en"},
            headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        )
    assert locale.status_code == 200
    assert locale.json() == {"locale": "en"}
    assert "set-cookie" not in locale.headers
    database.commit.assert_awaited_once()


def test_owner_logout_form_uses_the_protected_frontend_route():
    request = Request({"type": "http", "method": "GET", "path": "/dashboard", "headers": []})
    response = templates.TemplateResponse(request, "base.html", {"csrf_token": "rendered-proof"})

    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.forms = []

        def handle_starttag(self, tag, attrs):
            if tag == "form":
                self.forms.append(dict(attrs))

    parser = Forms()
    parser.feed(response.body.decode())
    assert any(form.get("method") == "post" and form.get("action") == "/logout" for form in parser.forms)
    assert 'name="csrf_token" value="rendered-proof"' in response.body.decode()
