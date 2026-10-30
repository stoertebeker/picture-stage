"""Authenticated page revalidation retains stronger endpoint cache policy."""

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Route

from app.security.middleware import SecurityHeadersMiddleware


async def test_existing_no_store_is_preserved_for_authenticated_html():
    async def private_page(_request):
        return HTMLResponse("private", headers={"Cache-Control": "no-store"})

    application = Starlette(routes=[Route("/", private_page)])
    application.add_middleware(SecurityHeadersMiddleware)
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        client.cookies.set("session", "opaque-session")
        response = await client.get("/")
    directives = set(response.headers["cache-control"].replace(" ", "").split(","))
    assert {"private", "no-cache", "no-store"} <= directives
