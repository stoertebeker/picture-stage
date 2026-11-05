"""0016 adds only an indexed, unique signup replay ledger and downgrades cleanly."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def test_captcha_migration_preserves_existing_schema_and_data():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0015")
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO revoked_tokens VALUES (:sig,now() + interval '5 minutes')"), {"sig": "a" * 64}
            )
            before = (await conn.execute(text("SELECT to_jsonb(t) FROM revoked_tokens t"))).scalars().all()
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0016")
            assert (await conn.execute(text("SELECT to_jsonb(t) FROM revoked_tokens t"))).scalars().all() == before
            assert await conn.scalar(text("SELECT count(*) FROM captcha_redemptions")) == 0
            indexes = (
                (await conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='captcha_redemptions'")))
                .scalars()
                .all()
            )
            assert set(indexes) == {"captcha_redemptions_pkey", "ix_captcha_redemptions_expires_at"}
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0015", downgrade=True)
            assert await conn.scalar(text("SELECT to_regclass('captcha_redemptions')")) is None
            assert (await conn.execute(text("SELECT to_jsonb(t) FROM revoked_tokens t"))).scalars().all() == before
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0016")
            assert await conn.scalar(text("SELECT count(*) FROM captcha_redemptions")) == 0
    finally:
        await _drop_everything(engine)
        await engine.dispose()
