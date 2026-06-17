"""Unit tests for the global request-body size limit middleware (picture-stage-m4ct).

Covers both gates of ``RequestBodySizeLimitMiddleware``: the Content-Length fast
path (reject before reading a byte) and the received-byte counter (catch a missing
Content-Length / chunked body mid-stream), plus the "0 = disabled" escape hatch and
pass-through for non-HTTP scopes. The Content-Length path is also exercised
end-to-end through a real ASGI stack with ``TestClient``. The limit is patched to a
small value so tests stay fast without allocating upload-sized payloads.
"""

from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.config import settings
from app.security.middleware import RequestBodySizeLimitMiddleware


def patch_setting(name: str, value: int):
    return patch.object(settings, name, value)


def http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "method": "POST", "path": "/", "headers": headers or []}


# --- Content-Length fast path -------------------------------------------------


@pytest.mark.asyncio
async def test_content_length_over_limit_rejected_before_app_runs() -> None:
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = RequestBodySizeLimitMiddleware(app)
    with patch_setting("max_request_body_mb", 1):
        scope = http_scope([(b"content-length", str(2 * 1024 * 1024).encode())])
        await mw(scope, receive, send)

    assert app_called is False  # rejected before the inner app ran
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_content_length_under_limit_passes_through() -> None:
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = RequestBodySizeLimitMiddleware(app)
    with patch_setting("max_request_body_mb", 10):
        await mw(http_scope([(b"content-length", b"1024")]), receive, send)

    assert app_called is True
    assert sent == []  # no 413 emitted by the middleware


# --- Received-byte counter (no / lying Content-Length) ------------------------


@pytest.mark.asyncio
async def test_byte_counter_over_limit_without_content_length() -> None:
    received_by_app: list[dict] = []

    async def app(scope, receive, send):
        # Drain the body like a real handler until a disconnect or last chunk.
        while True:
            message = await receive()
            received_by_app.append(message)
            if message["type"] == "http.disconnect" or not message.get("more_body"):
                break

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * (512 * 1024), "more_body": True},
            {"type": "http.request", "body": b"x" * (512 * 1024), "more_body": True},
            {"type": "http.request", "body": b"x" * (512 * 1024), "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    mw = RequestBodySizeLimitMiddleware(app)
    with patch_setting("max_request_body_mb", 1):  # 1 MB cap, body is 1.5 MB
        await mw(http_scope([]), receive, send)

    # The handler saw a disconnect once the cap was breached, and a 413 was sent.
    assert any(m["type"] == "http.disconnect" for m in received_by_app)
    assert sent and sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_malformed_content_length_falls_through_to_counter() -> None:
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True
        await receive()

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"hi", "more_body": False}

    mw = RequestBodySizeLimitMiddleware(app)
    with patch_setting("max_request_body_mb", 1):
        # Garbage Content-Length must not crash; small body passes the counter.
        await mw(http_scope([(b"content-length", b"not-a-number")]), receive, send)

    assert app_called is True
    assert sent == []


# --- Escape hatches -----------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_disables_the_limit() -> None:
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    async def send(message):
        pass

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mw = RequestBodySizeLimitMiddleware(app)
    with patch_setting("max_request_body_mb", 0):
        huge = str(99 * 1024 * 1024 * 1024).encode()
        await mw(http_scope([(b"content-length", huge)]), receive, send)

    assert app_called is True  # 0 = off, even an absurd Content-Length passes


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    async def send(message):
        pass

    async def receive():
        return {"type": "lifespan.startup"}

    mw = RequestBodySizeLimitMiddleware(app)
    with patch_setting("max_request_body_mb", 1):
        await mw({"type": "lifespan"}, receive, send)

    assert app_called is True  # only http scopes are size-limited


# --- End-to-end through a real ASGI stack -------------------------------------


def _build_app() -> Starlette:
    async def echo(request):
        body = await request.body()
        return PlainTextResponse(f"got {len(body)}")

    app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
    app.add_middleware(RequestBodySizeLimitMiddleware)
    return app


def test_testclient_large_body_returns_413() -> None:
    with patch_setting("max_request_body_mb", 1):
        client = TestClient(_build_app())
        response = client.post("/echo", content=b"x" * (2 * 1024 * 1024))
    assert response.status_code == 413


def test_testclient_small_body_passes() -> None:
    with patch_setting("max_request_body_mb", 1):
        client = TestClient(_build_app())
        response = client.post("/echo", content=b"hello")
    assert response.status_code == 200
    assert "got 5" in response.text
