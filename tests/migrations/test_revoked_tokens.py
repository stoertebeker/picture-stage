"""0013 -> 0014 adds only an empty indexed ledger, preserving existing data."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def test_revocation_migration_preserves_existing_accounts_and_data():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0013")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, status, tokens_valid_after) "
                    "VALUES (:id, 'legacy@example.com', 'unchanged-password', 'active', :cutoff)"
                ),
                {"id": uuid.uuid4(), "cutoff": datetime(2025, 1, 1, tzinfo=UTC)},
            )
            before = (await conn.execute(text("SELECT to_jsonb(u) FROM users u"))).all()
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0014")
            assert (await conn.execute(text("SELECT to_jsonb(u) FROM users u"))).all() == before
            assert await conn.scalar(text("SELECT count(*) FROM revoked_tokens")) == 0
            assert "expires_at" in await conn.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_revoked_tokens_expires_at'")
            )
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO revoked_tokens VALUES (:fingerprint, :expires)"),
                {"fingerprint": "a" * 64, "expires": datetime(2026, 11, 6, tzinfo=UTC)},
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO revoked_tokens VALUES (:fingerprint, now())"), {"fingerprint": "a" * 64}
                )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0013", downgrade=True)
            assert (await conn.execute(text("SELECT to_jsonb(u) FROM users u"))).all() == before
            assert await conn.scalar(text("SELECT to_regclass('public.revoked_tokens')")) is None
            await conn.run_sync(_migrate, "0014")
            assert await conn.scalar(text("SELECT count(*) FROM revoked_tokens")) == 0
    finally:
        await _drop_everything(engine)
        await engine.dispose()
