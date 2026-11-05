"""Exercise real cookie producers over direct HTTP without a database or lifespan."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.config import settings
from app.db.models import User, UserStatus
from app.db.session import get_db
from app.main import app


def _assert_cookie_security(response, names: tuple[str, ...], secure: bool) -> None:
    cookies = response.headers.get_list("set-cookie")
    for name in names:
        cookie = next(raw for raw in cookies if raw.startswith(f"{name}="))
        attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
        assert ("secure" in attributes) is secure
        assert "samesite=lax" in attributes
        assert "path=/" in attributes
        assert ("httponly" in attributes) is (name == "session")
    assert ("strict-transport-security" in response.headers) is secure


@pytest.mark.parametrize(
    ("environment", "app_url", "forwarded_proto", "secure"),
    [
        ("production", "https://photos.example.com", None, True),
        ("production", "https://photos.example.com", "http", True),
        ("development", "http://localhost:8000", None, False),
        ("development", "http://localhost:8000", "https", False),
        ("test", "http://localhost:8000", "https", False),
        # Model assignment intentionally bypasses startup validation. Production
        # and unknown modes must keep Secure/HSTS even in this unsafe state.
        ("production", "http://photos.example.com", "http", True),
        ("unknown", "http://photos.example.com", "http", True),
    ],
)
async def test_real_cookies_and_hsts_follow_configuration_over_direct_http(
    monkeypatch, environment: str, app_url: str, forwarded_proto: str | None, secure: bool
) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "app_url", app_url)
    user = User(
        id=uuid.uuid4(),
        email="transport-test@example.com",
        password_hash=hash_password("transport-test-password"),
        status=UserStatus.active,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    database = AsyncMock(spec=AsyncSession)
    database.execute.return_value = result

    async def fake_db():
        yield database

    monkeypatch.setitem(app.dependency_overrides, get_db, fake_db)
    headers = {"X-Forwarded-Proto": forwarded_proto} if forwarded_proto else {}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://direct-app.test", headers=headers
    ) as client:
        language = await client.get("/set-lang/en")
        assert language.status_code == 303
        _assert_cookie_security(language, ("csrf_token", "lang"), secure)

        # A TLS-terminating proxy can forward this cookie over HTTP. Supplying
        # it explicitly lets the test inspect Secure Set-Cookie on that path.
        csrf = language.cookies["csrf_token"]
        client.cookies.clear()
        cookie_header = {"Cookie": f"csrf_token={csrf}"}
        login = await client.post(
            "/login",
            headers=cookie_header,
            data={"email": user.email, "password": "transport-test-password", "csrf_token": csrf},
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/dashboard"
        _assert_cookie_security(login, ("session",), secure)

        client.cookies.clear()
        logout = await client.post("/logout", headers={**cookie_header, "X-CSRF-Token": csrf})
        assert logout.status_code == 303
        _assert_cookie_security(logout, ("session",), secure)

        api_logout = await client.post("/api/v1/auth/logout", headers={**cookie_header, "X-CSRF-Token": csrf})
        assert api_logout.status_code == 303
        _assert_cookie_security(api_logout, ("session",), secure)
