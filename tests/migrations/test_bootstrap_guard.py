"""Exercise the bootstrap guard installed by the real Alembic migration."""

import asyncio
import secrets
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.passwords import verify_password
from app.cli.recover_admin import RecoveryUnavailable, recover_admin
from app.config import settings
from app.db.models import SetupState, User, UserStatus
from app.db.session import get_db
from app.main import app
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def test_old_setup_race_and_offline_recovery_use_database_guard(monkeypatch):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(settings, "bootstrap_token", SecretStr(token))

    async def override_db():
        async with sessions() as db:
            yield db

    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0010")
        app.dependency_overrides[get_db] = override_db

        # An old version can observe the database while it is still empty.
        async with engine.connect() as old:
            assert await old.scalar(text("SELECT count(*) FROM users")) == 0
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                page = await client.get("/setup")
                csrf = page.cookies["csrf_token"]
                response = await client.post(
                    "/setup",
                    data={
                        "csrf_token": csrf,
                        "bootstrap_token": token,
                        "email": "new@example.com",
                        "password": "secure-password",
                        "password_confirm": "secure-password",
                    },
                )
                assert response.status_code == 303
            # Its later legacy INSERT has no transaction-local authorization.
            with pytest.raises(DBAPIError) as rejected:
                await old.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, status) "
                        "VALUES (:id, 'old@example.com', 'x', 'admin')"
                    ),
                    {"id": uuid.uuid4()},
                )
            assert rejected.value.orig.sqlstate == "42501"
            await old.rollback()

        async with sessions() as db:
            admin = await db.scalar(select(User))
            assert admin is not None and admin.email == "new@example.com"
            assert verify_password("secure-password", admin.password_hash)
            assert (await db.get(SetupState, 1)).completed

            # Normal approval inserts active users; promotion uses UPDATE.
            member = User(email="active@example.com", password_hash="x", status=UserStatus.active)
            db.add(member)
            await db.commit()
            member.status = UserStatus.admin
            await db.commit()
            assert await db.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.admin)) == 2
            with pytest.raises(RecoveryUnavailable):
                await recover_admin(db, "recovery@example.com", "secure-password")
            await db.rollback()

        # Operator-only recovery restores access without reopening public setup.
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM users"))
        async with sessions() as db:
            await recover_admin(db, "recovery@example.com", "recovery-password")
            permit = await db.scalar(text("SELECT current_setting('picture_stage.admin_insert', true)"))
            assert permit not in {"bootstrap", "recovery"}
        async with sessions() as db:
            recovered = await db.scalar(select(User))
            assert recovered is not None and recovered.email == "recovery@example.com"
            assert verify_password("recovery-password", recovered.password_hash)
            assert (await db.get(SetupState, 1)).completed

        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM users"))

        async def recovery_attempt(email):
            async with sessions() as db:
                try:
                    await recover_admin(db, email, "recovery-password")
                    return "created"
                except RecoveryUnavailable:
                    return "rejected"

        outcomes = await asyncio.gather(recovery_attempt("one@example.com"), recovery_attempt("two@example.com"))
        assert sorted(outcomes) == ["created", "rejected"]
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        await _drop_everything(engine)
        await engine.dispose()
