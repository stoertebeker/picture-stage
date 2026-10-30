"""Default dispatch, explicit limits and streaming at the admission boundary."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Mount, Route

from app.db.session import get_db
from app.main import app
from app.security.rate_limit import DefaultRateLimitMiddleware, limiter


@pytest.fixture(autouse=True)
def enabled_global_limiter(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", True)


async def test_undecorated_health_route_enforces_the_default_limit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(200):
            response = await client.get("/health")
            assert response.status_code == 200
        response = await client.get("/health")
    assert response.status_code == 429


def application(routes, active_limiter):
    instance = Starlette(routes=routes)
    instance.state.limiter = active_limiter
    instance.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    instance.add_middleware(DefaultRateLimitMiddleware)
    return instance


@pytest.fixture
def small_limiter():
    return Limiter(key_func=get_remote_address, default_limits=["2/minute"], storage_uri="memory://")


async def page(request):
    return PlainTextResponse("ok")


async def test_first_matching_route_controls_default_and_explicit_limits(small_limiter):
    @small_limiter.limit("1/minute")
    async def shadow(request: Request):
        return PlainTextResponse("shadow")

    instance = application([Route("/same", page), Route("/same", shadow)], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get("/same")).status_code for _ in range(3)] == [200, 200, 429]


async def test_explicit_limit_is_not_double_counted_or_replaced(small_limiter):
    @small_limiter.limit("3/minute")
    async def explicit(request: Request):
        return PlainTextResponse("ok")

    instance = application([Route("/explicit", explicit)], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get("/explicit")).status_code for _ in range(4)] == [200, 200, 200, 429]


async def test_matching_method_and_exempt_decorator_are_preserved(small_limiter):
    @small_limiter.limit("1/minute")
    async def post(request: Request):
        return PlainTextResponse("post")

    instance = application([Route("/same", page), Route("/same", post, methods=["POST"])], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get("/same")).status_code for _ in range(3)] == [200, 200, 429]
        assert [(await client.post("/same")).status_code for _ in range(2)] == [200, 429]

    small_limiter.reset()
    exempt = small_limiter.exempt(page)
    instance = application([Route("/exempt", exempt)], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get("/exempt")).status_code for _ in range(4)] == [200] * 4


async def test_spoofed_forwarding_headers_do_not_change_peer_bucket(small_limiter):
    instance = application([Route("/", page)], small_limiter)
    transport = ASGITransport(app=instance, client=("192.0.2.1", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        statuses = []
        for index in range(3):
            response = await client.get(
                "/", headers={"X-Forwarded-For": f"198.51.100.{index}", "CF-Connecting-IP": str(index)}
            )
            statuses.append(response.status_code)
        assert statuses == [200, 200, 429]
    async with AsyncClient(
        transport=ASGITransport(app=instance, client=("192.0.2.2", 1234)), base_url="http://test"
    ) as client:
        assert (await client.get("/")).status_code == 200


async def test_query_changes_share_existing_path_bucket(small_limiter):
    instance = application([Route("/", page)], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get(f"/?page={index}")).status_code for index in range(3)] == [200, 200, 429]


async def test_default_template_bucket_preserves_dispatched_path_and_parameters(small_limiter):
    async def reflected(request):
        return PlainTextResponse(f"{request.url.path}|{request.path_params['token']}")

    instance = application([Route("/public/{token}", reflected)], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        for token in ["first", "second"]:
            response = await client.get(f"/public/{token}")
            assert response.status_code == 200
            assert response.text == f"/public/{token}|{token}"
        assert (await client.get("/public/third")).status_code == 429


async def test_explicit_dynamic_route_keeps_its_concrete_path_scope(small_limiter):
    @small_limiter.limit("1/minute")
    async def explicit(request: Request):
        return PlainTextResponse(request.path_params["token"])

    instance = application([Route("/explicit/{token}", explicit)], small_limiter)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert (await client.get("/explicit/first")).status_code == 200
        assert (await client.get("/explicit/second")).status_code == 200
        assert (await client.get("/explicit/first")).status_code == 429


@pytest.mark.parametrize("explicit", [False, True])
async def test_nested_fastapi_includes_use_effective_prefix_and_handler(small_limiter, explicit):
    router = APIRouter()

    async def endpoint(request: Request):
        return PlainTextResponse(request.path_params["token"])

    handler = small_limiter.limit("1/minute")(endpoint) if explicit else endpoint
    router.add_api_route("/item/{token}", handler, methods=["GET"])
    parent = APIRouter()
    parent.include_router(router, prefix="/inner")
    instance = FastAPI()
    instance.include_router(parent, prefix="/outer")
    instance.state.limiter = small_limiter
    instance.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    instance.add_middleware(DefaultRateLimitMiddleware)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert (await client.get("/outer/inner/item/first")).text == "first"
        assert (await client.get("/outer/inner/item/second")).text == "second"
        # Default budget is shared across tokens; explicit budget remains scoped
        # to each concrete path. Both reject this repeated first token now.
        assert (await client.get("/outer/inner/item/first")).status_code == 429


async def test_disabled_limiter_and_existing_404_405_redirect_mounts(small_limiter):
    small_limiter.enabled = False
    instance = application(
        [Mount("/static", app=Starlette(routes=[Route("/file", page)])), Route("/page", page)], small_limiter
    )
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get("/page")).status_code for _ in range(4)] == [200] * 4
        small_limiter.enabled = True
        assert [(await client.get("/static/file")).status_code for _ in range(4)] == [200] * 4
        assert (await client.get("/missing")).status_code == 404
        assert (await client.post("/page")).status_code == 405
        assert (await client.get("/page/")).status_code == 307


async def test_native_environment_off_switch(monkeypatch):
    monkeypatch.setenv("RATELIMIT_ENABLED", "false")
    configured = Limiter(key_func=get_remote_address, default_limits=["1/minute"])
    instance = application([Route("/", page)], configured)
    async with AsyncClient(transport=ASGITransport(app=instance), base_url="http://test") as client:
        assert [(await client.get("/")).status_code for _ in range(3)] == [200] * 3


@pytest.mark.parametrize("headers_enabled", [False, True])
async def test_streams_emit_one_start_and_preserve_all_chunks(headers_enabled):
    active_limiter = Limiter(key_func=get_remote_address, default_limits=["2/minute"], headers_enabled=headers_enabled)

    async def streaming(request):
        async def chunks():
            yield b"first"
            yield b"second"
            yield b"third"

        return StreamingResponse(chunks())

    instance = application([Route("/stream", streaming)], active_limiter)
    transport = ASGITransport(app=instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(2):
            response = await client.get("/stream")
            assert response.status_code == 200
            assert response.content == b"firstsecondthird"
            assert ("X-RateLimit-Limit" in response.headers) == headers_enabled
        assert (await client.get("/stream")).status_code == 429


@pytest.mark.parametrize("path", ["/g/invalid-token", "/verify-email/invalid-token"])
@pytest.mark.parametrize("rotate_token", [False, True])
async def test_real_public_html_routes_are_limited_before_database_work(monkeypatch, path, rotate_token):
    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=MagicMock())
    fake_db.execute.return_value.scalars.return_value.all.return_value = []

    async def override_db():
        yield fake_db

    monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
    monkeypatch.setattr(limiter, "enabled", True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for index in range(200):
            attempted_path = f"{path}-{index}" if rotate_token else path
            assert (await client.get(attempted_path)).status_code == 404
        before = fake_db.execute.await_count
        response = await client.get(path + "-new" if rotate_token else path)
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["error"]
    assert fake_db.execute.await_count == before
