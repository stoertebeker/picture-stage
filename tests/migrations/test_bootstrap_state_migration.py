"""Bootstrap claim migration initializes safely for fresh and existing DBs."""

import uuid

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.config import settings
from tests.migrations.test_migration_drift import _drop_everything


def _migrate(connection, revision, *, downgrade=False):
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = connection
    (command.downgrade if downgrade else command.upgrade)(cfg, revision)


@pytest.mark.parametrize("existing_user", [False, True])
async def test_bootstrap_migration_preserves_and_consumes_existing_installations(existing_user):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0009")
        if existing_user:
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO users (id, email, password_hash) VALUES (:id, 'old@example.com', 'hash')"),
                    {"id": uuid.uuid4()},
                )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0010")
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT completed FROM setup_state WHERE id = 1")) is existing_user
            assert await conn.scalar(text("SELECT count(*) FROM users")) == int(existing_user)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO setup_state (id, completed) VALUES (2, false)"))
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0009", downgrade=True)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM users")) == int(existing_user)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0010")
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT completed FROM setup_state WHERE id = 1")) is existing_user
    finally:
        await _drop_everything(engine)
        await engine.dispose()
