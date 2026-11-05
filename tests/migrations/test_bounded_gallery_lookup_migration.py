"""Normalize old locators and preserve classification under rolling writers."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.passwords import hash_token, verify_token
from app.auth.token_lookup import token_locator
from app.auth.tokens import generate_verification_token
from app.config import settings
from tests.migrations.test_migration_drift import _drop_everything
from tests.migrations.test_token_lookup_migration import _migrate


async def test_classification_backfill_rolling_writers_and_rollback():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    owner_id = uuid.uuid4()
    tokens = ["replayable", "hash-only", "mismatched-raw", generate_verification_token()]
    ids = [uuid.uuid4() for _ in tokens]
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0016")
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id,email,password_hash) VALUES (:id,'bounded@example.com','unused')"),
                {"id": owner_id},
            )
            for number, token in enumerate(tokens):
                digest, salt = hash_token(token)
                await conn.execute(
                    text(
                        "INSERT INTO galleries (id,owner_id,name,share_token,share_token_hash,share_token_salt,"
                        "share_token_lookup) VALUES (:id,:owner,'legacy',:raw,:digest,:salt,:locator)"
                    ),
                    {
                        "id": ids[number],
                        "owner": owner_id,
                        "raw": None if number == 1 else "wrong-plaintext" if number == 2 else token,
                        "digest": digest,
                        "salt": salt,
                        "locator": token_locator("stale-non-null"),
                    },
                )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0017")
        async with engine.begin() as conn:
            locators = dict((await conn.execute(text("SELECT id,share_token_lookup FROM galleries"))).all())
            assert locators == {
                ids[0]: token_locator(tokens[0]),
                ids[1]: None,
                ids[2]: None,
                ids[3]: token_locator(tokens[3]),
            }
            # An old app writes credentials without knowing the locator column.
            digest, salt = hash_token("rolling-replacement")
            await conn.execute(
                text(
                    "UPDATE galleries SET share_token='rolling-replacement',share_token_hash=:digest,"
                    "share_token_salt=:salt WHERE id=:id"
                ),
                {"digest": digest, "salt": salt, "id": ids[0]},
            )
            assert await conn.scalar(
                text("SELECT share_token_lookup FROM galleries WHERE id=:id"), {"id": ids[0]}
            ) == token_locator("rolling-replacement")
            await conn.execute(text("UPDATE galleries SET share_token=NULL WHERE id=:id"), {"id": ids[0]})
            assert (
                await conn.scalar(text("SELECT share_token_lookup FROM galleries WHERE id=:id"), {"id": ids[0]}) is None
            )
            await conn.execute(
                text("UPDATE galleries SET share_token_lookup=:bad WHERE id=:id"),
                {"id": ids[1], "bad": token_locator("wrong")},
            )
            assert (
                await conn.scalar(text("SELECT share_token_lookup FROM galleries WHERE id=:id"), {"id": ids[1]}) is None
            )
            indexes = (
                (
                    await conn.execute(
                        text("SELECT indexdef FROM pg_indexes WHERE tablename='galleries' AND indexname LIKE '%token%'")
                    )
                )
                .scalars()
                .all()
            )
            assert any("share_token_lookup, id" in value for value in indexes)
            assert any(
                "WHERE ((share_token_lookup IS NULL) AND (share_token_hash IS NOT NULL))" in value for value in indexes
            )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0016", downgrade=True)
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id,share_token_hash,share_token_salt FROM galleries"))).all()
            for row in rows:
                expected = "rolling-replacement" if row.id == ids[0] else tokens[ids.index(row.id)]
                assert verify_token(expected, row.share_token_hash, row.share_token_salt)
            await conn.run_sync(_migrate, "0017")
    finally:
        await _drop_everything(engine)
        await engine.dispose()
