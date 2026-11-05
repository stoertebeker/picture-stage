"""Verification index bounds generic plans without rewriting old credentials."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.passwords import hash_token, verify_token
from app.auth.tokens import generate_verification_token, verification_lookup_id
from app.config import settings
from tests.migrations.test_migration_drift import _drop_everything
from tests.migrations.test_token_lookup_migration import _migrate


async def test_verification_index_preserves_bytes_and_generic_plan_bounds_candidates():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    token = generate_verification_token()
    digest, salt = hash_token(token)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0018")
        async with engine.begin() as conn:
            for number, locator in ((-1, None), (0, verification_lookup_id(token)), (2**31 - 1, None)):
                await conn.execute(
                    text(
                        "INSERT INTO pending_signups "
                        "(id,email,password_hash,verification_token_hash,"
                        "verification_token_salt,verification_lookup_id) "
                        "VALUES (:id,:email,'old-password',:digest,:salt,:locator)"
                    ),
                    {
                        "id": number,
                        "email": f"migration-{number}@example.com",
                        "digest": digest,
                        "salt": salt,
                        "locator": locator,
                    },
                )
            before = (await conn.execute(text("SELECT * FROM pending_signups ORDER BY id"))).all()
            # A large indexed population must not be traversed, while cleared
            # legacy credentials still count as physical candidates.
            await conn.execute(
                text("""
                INSERT INTO pending_signups
                    (id,email,password_hash,verification_token_hash,verification_token_salt,verification_lookup_id)
                SELECT n, 'indexed-' || n || '@example.com', 'old-password', '\\x'::bytea, '\\x'::bytea,
                       gen_random_uuid() FROM generate_series(1,10000) n
            """)
            )
            await conn.execute(
                text("""
                INSERT INTO pending_signups
                    (id,email,password_hash,verification_token_hash,verification_token_salt)
                SELECT n, 'cleared-' || n || '@example.com', 'old-password', '\\x'::bytea, '\\x'::bytea
                FROM generate_series(10001,10200) n
            """)
            )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0019")
        async with engine.begin() as conn:
            after = (
                await conn.execute(text("SELECT * FROM pending_signups WHERE id <= 0 OR id=2147483647 ORDER BY id"))
            ).all()
            assert before == after
            assert all(verify_token(token, row.verification_token_hash, row.verification_token_salt) for row in after)
            assert all(row.email_verified_at is None and row.password_hash == "old-password" for row in after)
            index = await conn.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_pending_signups_legacy_token_candidates'")
            )
            assert "(id) WHERE (verification_lookup_id IS NULL)" in index
            await conn.execute(text("ANALYZE pending_signups"))
            await conn.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
            await conn.execute(
                text(
                    "PREPARE verification_window(integer, integer) AS SELECT id FROM pending_signups "
                    "WHERE verification_lookup_id IS NULL AND id > $1 ORDER BY id LIMIT $2"
                )
            )
            plan = await conn.scalar(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE verification_window(0,65)"))
            nodes = [plan[0]["Plan"]]
            scans = []
            while nodes:
                node = nodes.pop()
                nodes.extend(node.get("Plans", []))
                if "Scan" in node["Node Type"]:
                    scans.append(node)
            assert len(scans) == 1
            assert scans[0]["Index Name"] == "ix_pending_signups_legacy_token_candidates"
            assert scans[0]["Actual Rows"] == 65
            assert scans[0].get("Rows Removed by Filter", 0) == 0
            assert scans[0].get("Rows Removed by Index Recheck", 0) == 0
            await conn.execute(text("DEALLOCATE verification_window"))
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0018", downgrade=True)
            assert (
                await conn.scalar(
                    text("SELECT count(*) FROM pg_indexes WHERE indexname='ix_pending_signups_legacy_token_candidates'")
                )
                == 0
            )
            await conn.run_sync(_migrate, "0019")
            final = (
                await conn.execute(text("SELECT * FROM pending_signups WHERE id <= 0 OR id=2147483647 ORDER BY id"))
            ).all()
            assert final == before
    finally:
        await _drop_everything(engine)
        await engine.dispose()
