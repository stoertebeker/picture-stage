"""Selection writes retain an early body limit even if global limits are off."""

import json
import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.config import settings
from app.security.middleware import RequestBodySizeLimitMiddleware


async def test_declared_oversize_rejected_before_body_and_root_path_normalized():
    async def app(scope, receive, send):
        pytest.fail("Application must not run for a declared oversized selection body")

    async def receive():
        pytest.fail("Oversized declared body must not be read")

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "root_path": "/prefix",
        "path": "/prefix/g/token/selections",
        "raw_path": b"/prefix/%67/token/%73elections",
        "headers": [(b"content-length", b"65537")],
    }
    with patch.object(settings, "max_request_body_mb", 0), patch.object(settings, "max_non_upload_request_body_kb", 0):
        await RequestBodySizeLimitMiddleware(app)(scope, receive, send)
    assert sent[0]["status"] == 413


async def test_chunked_selection_body_is_counted_when_global_limits_are_off():
    async def app(scope, receive, send):
        while (message := await receive())["type"] != "http.disconnect":
            if not message.get("more_body", False):
                break

    messages = iter(
        [
            {"type": "http.request", "body": b"x" * 32768, "more_body": True},
            {"type": "http.request", "body": b"x" * 32769, "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/g/token/selections", "headers": []}
    with patch.object(settings, "max_request_body_mb", 0), patch.object(settings, "max_non_upload_request_body_kb", 0):
        await RequestBodySizeLimitMiddleware(app)(scope, receive, send)
    assert sent[0]["status"] == 413


async def test_escaped_unicode_comment_passes_and_encoded_path_is_capped():
    async def endpoint(request):
        return PlainTextResponse(str(len(await request.body())))

    app = Starlette(routes=[Route("/g/{token}/selections", endpoint, methods=["POST"])])
    app.add_middleware(RequestBodySizeLimitMiddleware)
    payload = json.dumps(
        {"image_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "action": "comment", "comment": "🙂" * 6000}
    ).encode()
    assert len(payload) > 65536  # non-BMP JSON escapes use 12 bytes per character
    with (
        patch.object(settings, "max_request_body_mb", 0),
        patch.object(settings, "max_non_upload_request_body_kb", 0),
        patch.object(settings, "max_selection_comment_chars", 6000),
    ):
        async with AsyncClient(transport=ASGITransport(app=app, root_path="/prefix"), base_url="http://test") as client:
            response = await client.post("/prefix/%67/token/%73elections", content=payload)
            assert response.status_code == 200
            over = await client.post("/prefix/%67/token/%73elections", content=b"x" * 76100)
            assert over.status_code == 413
            other = await client.post("/prefix/g/token/not-selections", content=b"x" * 76100)
            assert other.status_code == 404
