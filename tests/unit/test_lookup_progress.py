import uuid
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.auth import lookup_progress as lp


def _context(cookie: str = "", *, root: str = ""):
    return lp.LookupContext(
        Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "path": root + "/g/token",
                "root_path": root,
                "query_string": b"",
                "headers": [(b"cookie", cookie.encode())],
            }
        )
    )


def test_progress_is_token_purpose_bound_bounded_and_expiring(monkeypatch):
    monkeypatch.setattr(lp.time, "time", lambda: 1000)
    ctx = _context()
    reset = lp._context.set(ctx)
    try:
        progress = lp.LookupProgress("gallery", "token", "/g/token")
        key = uuid.uuid4().hex
        progress.save("l", key)
        value = next(iter(ctx.cookies.values()))
        ctx.request = _context(f"gallery_lookup={value}").request
        assert progress.read() == ("l", key)
        assert lp.LookupProgress("gallery", "other", "/g/token").read() is None
        ctx.request = _context(f"verification_lookup={value}").request
        assert lp.LookupProgress("verification", "token", "/g/token").read() is None
        for invalid in (value[:-1] + "z", "l." + "9" * 5000, "h.z.999999999999.abc", value.replace("l.", "x.", 1)):
            ctx.request = _context(f"gallery_lookup={invalid}").request
            assert progress.read() is None
        ctx.request = _context(f"gallery_lookup={value}").request
        monkeypatch.setattr(lp.time, "time", lambda: 1000 + lp.LOOKUP_TTL)
        assert progress.read() is None
        progress.save("l", key)
        ctx.request = _context(f"gallery_lookup={next(iter(ctx.cookies.values()))}").request
        assert progress.read() == ("l", key)
    finally:
        lp._context.reset(reset)


@pytest.mark.asyncio
async def test_exception_response_receives_progress_cookie_with_root_and_security(monkeypatch):
    monkeypatch.setattr(lp.settings, "app_url", "https://test")
    app = FastAPI(root_path="/mount")
    app.add_middleware(lp.LookupProgressMiddleware)

    @app.get("/g/token")
    async def retry():
        lp.LookupProgress("gallery", "token", "/g/token").retry("l", uuid.UUID(int=1).hex)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/mount/g/token")
    assert response.status_code == 503
    assert response.headers[lp.RETRY_HEADER] == lp.RETRY_VALUE
    cookie = response.headers["set-cookie"]
    assert "Path=/mount/g/token" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
    assert lp._context.get() is None


@pytest.mark.parametrize("root", ["", "/mount"])
@pytest.mark.parametrize("purpose", ["gallery", "verification"])
@pytest.mark.parametrize("suffix", ["", "%61"])
async def test_encoded_alias_returns_progress_cookie_on_the_actual_raw_route(root, purpose, suffix):
    app = FastAPI(root_path=root)
    app.add_middleware(lp.LookupProgressMiddleware)
    route = "/g/{token}" if purpose == "gallery" else "/verify-email/{token}"

    @app.get(route)
    async def resolve(token: str):
        progress = lp.LookupProgress(purpose, token, "/g/" + quote(token, safe="") if purpose == "gallery" else None)
        if progress.read() is None:
            progress.retry("l", uuid.UUID(int=1).hex)
        return {"resolved": True}

    alias = root + ("/%67/%6cegacy" if purpose == "gallery" else "/verify-email/%6cegacy") + quote(suffix, safe="")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(alias)
        second = await client.get(alias)
    assert first.status_code == 503
    assert f"Path={alias}" in first.headers["set-cookie"]
    assert second.status_code == 200
