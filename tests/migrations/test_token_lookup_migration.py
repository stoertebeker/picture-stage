"""Upgrade and rollback preserve both replayable and hash-only token records."""

import hashlib
import secrets
import uuid

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.auth.passwords import hash_token, verify_token
from app.auth.tokens import generate_verification_token, verification_lookup_id
from app.config import settings
from tests.migrations.test_migration_drift import _drop_everything


def _migrate(connection, revision: str, *, downgrade: bool = False) -> None:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    if downgrade:
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


async def test_token_locator_migration_backfill_and_rollback():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    owner_id, shared_id, hash_only_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    shared_token, hash_only_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    shared_hash, shared_salt = hash_token(shared_token)
    legacy_hash, legacy_salt = hash_token(hash_only_token)
    new_token = generate_verification_token()
    pending_hash, pending_salt = hash_token(new_token)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0008")
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, 'migration@example.com', 'hash')"),
                {"id": owner_id},
            )
            for gallery_id, raw, digest, salt in (
                (shared_id, shared_token, shared_hash, shared_salt),
                (hash_only_id, None, legacy_hash, legacy_salt),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO galleries (id, owner_id, name, share_token, share_token_hash, share_token_salt) "
                        "VALUES (:id, :owner, 'test', :raw, :digest, :salt)"
                    ),
                    {"id": gallery_id, "owner": owner_id, "raw": raw, "digest": digest, "salt": salt},
                )
            await conn.execute(
                text(
                    "INSERT INTO pending_signups (email, password_hash, verification_token_hash, "
                    "verification_token_salt) "
                    "VALUES ('pending@example.com', 'hash', :digest, :salt)"
                ),
                {"digest": pending_hash, "salt": pending_salt},
            )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0009")
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT id, share_token_lookup FROM galleries"))
            locators = dict(result.all())
            assert locators[shared_id] == hashlib.sha256(shared_token.encode()).digest()
            assert locators[hash_only_id] is None
            await conn.execute(
                text("UPDATE pending_signups SET verification_lookup_id = :locator"),
                {"locator": verification_lookup_id(new_token)},
            )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0008", downgrade=True)
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT share_token, share_token_hash, share_token_salt FROM galleries WHERE id=:id"),
                {"id": shared_id},
            )
            raw, digest, salt = result.one()
            assert raw == shared_token and verify_token(shared_token, digest, salt)
            result = await conn.execute(
                text("SELECT verification_token_hash, verification_token_salt FROM pending_signups")
            )
            digest, salt = result.one()
            assert verify_token(new_token, digest, salt)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0009")
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT verification_lookup_id FROM pending_signups")) is None
            result = await conn.execute(
                text("SELECT verification_token_hash, verification_token_salt FROM pending_signups")
            )
            digest, salt = result.one()
            assert verify_token(new_token, digest, salt)
    finally:
        await _drop_everything(engine)
        await engine.dispose()
