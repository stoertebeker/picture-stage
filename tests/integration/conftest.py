"""Fixtures for v0.3 integration tests (run against real PostgreSQL in CI).

These tests exercise DB-backed request flows end-to-end via httpx ASGITransport
(in-process, no real network). They require a live PostgreSQL reachable via
DATABASE_URL — provided by the CI postgres service container. They will not run
locally inside the sandbox (loopback to the DB is blocked).

Design notes:
- A dedicated engine with NullPool is used: asyncpg connections must not be
  shared across event loops, and NullPool avoids "attached to a different loop"
  errors when pytest-asyncio creates a fresh loop per test.
- get_db is overridden so request handlers use the test sessionmaker.
- Tables are dropped+created around every test for full isolation.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.passwords import hash_password
from app.auth.tokens import create_access_token
from app.config import settings
from app.db import models  # noqa: F401  -- ensure all tables are registered
from app.db.base import Base
from app.db.models import User, UserStatus
from app.db.session import get_db
from app.main import app

test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def _override_get_db():
    async with TestSession() as session:
        yield session


# Request handlers use the test sessionmaker for the whole test run.
app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture(autouse=True)
async def _reset_schema():
    """Drop + recreate all tables around each test for isolation."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db():
    """A standalone session for arranging test data directly in the DB."""
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    """In-process HTTP client (no real network, no lifespan)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def make_user(db, email: str, status: UserStatus = UserStatus.active) -> User:
    """Create and persist a user, returning the refreshed instance."""
    user = User(email=email, password_hash=hash_password("pw-not-used"), status=status)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.fixture
def auth_headers():
    """Returns a factory: auth_headers(user) -> Bearer-auth headers."""

    def _make(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    return _make


@pytest_asyncio.fixture
async def owner(db) -> User:
    return await make_user(db, "owner@test.local")


@pytest_asyncio.fixture
async def other_user(db) -> User:
    return await make_user(db, "intruder@test.local")
