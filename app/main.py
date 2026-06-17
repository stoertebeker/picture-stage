import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.config import settings
from app.frontend.admin import router as frontend_admin_router
from app.frontend.auth import router as frontend_auth_router
from app.frontend.dashboard import router as frontend_dashboard_router
from app.frontend.galleries import router as frontend_galleries_router
from app.frontend.guest import router as frontend_guest_router
from app.frontend.legal import router as frontend_legal_router
from app.frontend.setup import router as frontend_setup_router
from app.galleries.export import router as export_router
from app.galleries.router import router as galleries_router
from app.galleries.sharing import router as sharing_router
from app.guest.router import router as guest_router
from app.i18n import detect_locale
from app.images.router import router as images_router
from app.notifications.router import router as notifications_router
from app.security.middleware import CSRFMiddleware, RequestBodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if settings.secret_key == "CHANGE_ME":  # noqa: S105
        logger.warning("SECRET_KEY is not set — using insecure default. Set it in .env before deploying.")
    if settings.hmac_secret_key == "CHANGE_ME":  # noqa: S105
        logger.warning("HMAC_SECRET_KEY is not set — using insecure default. Set it in .env before deploying.")

    from app.db import models as _models  # noqa: F401
    from app.db.migrations import run_migrations
    from app.images.process_pool import shutdown_pool

    await run_migrations()

    try:
        yield
    finally:
        # Tear down the preview ProcessPool so no worker processes are orphaned.
        shutdown_pool()


app = FastAPI(
    title="Picture-Stage",
    description="Self-hosted photo proofing for photographers and models",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


class LocaleMiddleware(BaseHTTPMiddleware):
    """Detect locale per request and store it in request.state.locale."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.locale = detect_locale(request)
        response = await call_next(request)
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(LocaleMiddleware)
# Outermost middleware (last added runs first): cap the request body before any
# other layer reads it — in particular before CSRF calls request.body() (m4ct).
app.add_middleware(RequestBodySizeLimitMiddleware)

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(galleries_router)
app.include_router(sharing_router)
app.include_router(export_router)
app.include_router(images_router)
app.include_router(frontend_guest_router)
app.include_router(guest_router)
app.include_router(notifications_router)
app.include_router(frontend_auth_router)
app.include_router(frontend_dashboard_router)
app.include_router(frontend_galleries_router)
app.include_router(frontend_admin_router)
app.include_router(frontend_setup_router)
app.include_router(frontend_legal_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/set-lang/{locale}")
async def set_language(locale: str, request: Request) -> Response:
    """Set language cookie and redirect back to the referring page."""
    from app.i18n import get_supported_locales

    if locale not in get_supported_locales():
        locale = "de"

    referer = request.headers.get("referer", "/")
    response = RedirectResponse(url=referer, status_code=303)
    response.set_cookie(
        key="lang",
        value=locale,
        max_age=365 * 24 * 3600,  # 1 year
        httponly=False,  # needs to be readable by JS for language switcher state
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response
