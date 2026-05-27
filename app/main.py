import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.config import settings
from app.db.base import Base, engine
from app.frontend.admin import router as frontend_admin_router
from app.frontend.auth import router as frontend_auth_router
from app.frontend.dashboard import router as frontend_dashboard_router
from app.frontend.galleries import router as frontend_galleries_router
from app.frontend.guest import router as frontend_guest_router
from app.frontend.setup import router as frontend_setup_router
from app.galleries.export import router as export_router
from app.galleries.router import router as galleries_router
from app.galleries.sharing import router as sharing_router
from app.guest.router import router as guest_router
from app.images.router import router as images_router
from app.notifications.router import router as notifications_router
from app.security.middleware import CSRFMiddleware, SecurityHeadersMiddleware
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if settings.secret_key == "CHANGE_ME":  # noqa: S105
        logger.warning("SECRET_KEY is not set — using insecure default. Set it in .env before deploying.")
    if settings.hmac_secret_key == "CHANGE_ME":  # noqa: S105
        logger.warning("HMAC_SECRET_KEY is not set — using insecure default. Set it in .env before deploying.")

    from app.db import models as _models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield


app = FastAPI(
    title="Picture-Stage",
    description="Self-hosted photo proofing for photographers and models",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)

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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
