"""Pending signup verification migration must never invent historical proof."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.passwords import hash_token
from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def _legacy_snapshot(connection):
    queries = {
        "pending_signups": "SELECT to_jsonb(p) - 'email_verified_at' FROM pending_signups p ORDER BY p.id",
        "users": "SELECT to_jsonb(u) FROM users u ORDER BY u.id",
        "columns": (
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' "
            "AND NOT (table_name = 'pending_signups' AND column_name = 'email_verified_at') "
            "ORDER BY table_name, column_name"
        ),
    }
    snapshot = {}
    for table, query in queries.items():
        snapshot[table] = (await connection.execute(text(query))).all()
    return snapshot


async def test_signup_email_verified_at_preserves_legacy_data_without_backfill():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    requested_at = datetime(2024, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
    user_verified_at = datetime(2025, 4, 5, 6, 7, 8, 654321, tzinfo=UTC)
    indexed_hash, indexed_salt = hash_token("existing-indexed-signup")
    opaque_hash, opaque_salt = hash_token("existing-opaque-signup")
    fixtures = (
        ("indexed", indexed_hash, indexed_salt, uuid.uuid4()),
        ("opaque", opaque_hash, opaque_salt, None),
        ("consumed", b"", b"", None),
        ("missing-hash", b"", b"remaining-salt", None),
        ("missing-salt", b"remaining-hash", b"", uuid.uuid4()),
        ("malformed", b"short-hash", b"short-salt", uuid.uuid4()),
    )
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0011")
        # Use the actual old schema, without depending on the newer ORM model.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, status, email_verified_at) "
                    "VALUES (:id, 'existing-user@example.com', 'existing-user-password', 'active', :verified)"
                ),
                {"id": uuid.uuid4(), "verified": user_verified_at},
            )
            for index, (name, digest, salt, locator) in enumerate(fixtures):
                await conn.execute(
                    text(
                        "INSERT INTO pending_signups (email, password_hash, verification_token_hash, "
                        "verification_token_salt, verification_lookup_id, requested_at, ip_address) "
                        "VALUES (:email, :password, :digest, :salt, :locator, :requested, :ip)"
                    ),
                    {
                        "email": f"{name}@example.com",
                        "password": f"existing-password-{index}",
                        "digest": digest,
                        "salt": salt,
                        "locator": locator,
                        "requested": requested_at + timedelta(days=index),
                        "ip": f"192.0.2.{index + 1}" if index else None,
                    },
                )
            before_upgrade = await _legacy_snapshot(conn)

        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0012")
        async with engine.connect() as conn:
            assert await _legacy_snapshot(conn) == before_upgrade
            assert (
                await conn.execute(text("SELECT email_verified_at FROM pending_signups ORDER BY id"))
            ).scalars().all() == [None] * len(fixtures)
            column = (
                await conn.execute(
                    text(
                        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'pending_signups' "
                        "AND column_name = 'email_verified_at'"
                    )
                )
            ).one()
            assert column.data_type == "timestamp with time zone"
            assert column.is_nullable == "YES"
            assert column.column_default is None
            assert await conn.scalar(text("SELECT email_verified_at FROM users")) == user_verified_at

        async with engine.begin() as conn:
            # Omitted verification time must remain NULL, even for empty tokens.
            new_signup = (
                await conn.execute(
                    text(
                        "INSERT INTO pending_signups (email, password_hash, verification_token_hash, "
                        "verification_token_salt) VALUES ('new@example.com', 'new-password', :empty, :empty) "
                        "RETURNING id, email_verified_at"
                    ),
                    {"empty": b""},
                )
            ).one()
            assert new_signup.email_verified_at is None
            verified_at = datetime(2026, 7, 8, 9, 10, 11, 987654, tzinfo=timezone(timedelta(hours=5, minutes=30)))
            assert (
                await conn.scalar(
                    text(
                        "UPDATE pending_signups SET email_verified_at = :verified WHERE id = :id "
                        "RETURNING email_verified_at"
                    ),
                    {"verified": verified_at, "id": new_signup.id},
                )
                == verified_at
            )
            before_downgrade = await _legacy_snapshot(conn)

        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0011", downgrade=True)
        async with engine.connect() as conn:
            assert await _legacy_snapshot(conn) == before_downgrade
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name = 'pending_signups' AND column_name = 'email_verified_at'"
                    )
                )
                == 0
            )

        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0012")
        async with engine.connect() as conn:
            assert await _legacy_snapshot(conn) == before_downgrade
            # Even the row that had explicit proof before downgrade gets no
            # reconstructed timestamp from empty verifiers or requested_at.
            assert (
                await conn.execute(text("SELECT email_verified_at FROM pending_signups ORDER BY id"))
            ).scalars().all() == [None] * (len(fixtures) + 1)
            assert await conn.scalar(text("SELECT email_verified_at FROM users")) == user_verified_at
    finally:
        await _drop_everything(engine)
        await engine.dispose()
