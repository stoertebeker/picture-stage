"""Request admission through the real middleware stack, without a database."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.config import settings
from app.main import app


async def test_auth_body_over_one_mib_is_rejected_before_json_parsing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/signup", content=b" " * (1024 * 1024 + 1), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413


@pytest.mark.parametrize("path", ["/api/v1/auth/signup", "/api/v1/auth/login", "/login", "/signup", "/setup"])
@pytest.mark.parametrize("length", [None, "1", "invalid"])
async def test_chunked_auth_overflow_returns_one_413_before_parsing(monkeypatch, path, length):
    monkeypatch.setattr(settings, "max_non_upload_request_body_kb", 1)

    async def unexpected_json(_request):
        pytest.fail("Oversized request reached JSON parsing")

    monkeypatch.setattr(Request, "json", unexpected_json)

    async def chunks():
        yield b"padding=" + b"x" * 500
        yield b"x" * 800

    headers = {"Content-Type": "application/json" if path.startswith("/api/") else "application/x-www-form-urlencoded"}
    if length is not None:
        headers["Content-Length"] = length
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("csrf_token", "test-csrf")
        response = await client.post(path, content=chunks(), headers=headers)
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


async def test_small_invalid_json_still_reaches_normal_validation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/signup", content=b"invalid", headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422


@pytest.mark.parametrize("gallery_id", [str(uuid.UUID(int=1)), uuid.UUID(int=1).hex, uuid.UUID(int=1).urn])
@pytest.mark.parametrize("frontend", [False, True])
@pytest.mark.parametrize("suffix", ["", "/"])
async def test_both_upload_routes_keep_larger_budget_and_authorization(monkeypatch, gallery_id, frontend, suffix):
    monkeypatch.setattr(settings, "max_non_upload_request_body_kb", 1)
    path = f"/galleries/{gallery_id}/upload" if frontend else f"/api/v1/galleries/{gallery_id}/images"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("csrf_token", "test-csrf")
        response = await client.post(
            path + suffix,
            files={"files": ("tiny.png", b"x" * 2048, "image/png")},
            headers={"X-CSRF-Token": "test-csrf"},
        )
    expected = {307} if suffix else ({303} if frontend else {401, 403})
    assert response.status_code in expected


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", f"/api/v1/galleries/{uuid.UUID(int=1)}/images"),
        ("POST", "/api/v1/galleries/invalid/images"),
        ("POST", f"/api/v1/galleries/{uuid.UUID(int=1)}/images/bulk-delete"),
        ("POST", f"/galleries/{uuid.UUID(int=1)}/upload/extra"),
    ],
)
async def test_upload_neighbors_do_not_inherit_upload_budget(monkeypatch, method, path):
    monkeypatch.setattr(settings, "max_non_upload_request_body_kb", 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, content=b"x" * 2048)
    assert response.status_code == 413


async def test_upload_budget_respects_mount_root(monkeypatch):
    monkeypatch.setattr(settings, "max_non_upload_request_body_kb", 1)
    transport = ASGITransport(app=app, root_path="/photos")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/photos/api/v1/galleries/{uuid.UUID(int=1)}/images",
            files={"files": ("tiny.png", b"x" * 2048, "image/png")},
        )
    assert response.status_code in {401, 403}
