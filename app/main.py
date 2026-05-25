import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.auth.startup import create_initial_admin
from app.config import settings
from app.galleries.export import router as export_router
from app.galleries.router import router as galleries_router
from app.galleries.sharing import router as sharing_router
from app.guest.router import router as guest_router
from app.images.router import router as images_router
from app.security.middleware import SecurityHeadersMiddleware
from app.security.rate_limit import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if settings.secret_key == "CHANGE_ME":
        logger.warning("SECRET_KEY is not set — using insecure default. Set it in .env before deploying.")
    if settings.hmac_secret_key == "CHANGE_ME":
        logger.warning("HMAC_SECRET_KEY is not set — using insecure default. Set it in .env before deploying.")

    await create_initial_admin()
    yield


app = FastAPI(
    title="Picture-Stage",
    description="Self-hosted photo proofing for photographers and models",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(galleries_router)
app.include_router(sharing_router)
app.include_router(export_router)
app.include_router(images_router)
app.include_router(guest_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
