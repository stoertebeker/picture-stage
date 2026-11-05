"""Authorization epochs preserve existing links, sessions and selection history."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.passwords import hash_token
from app.auth.token_lookup import token_locator
from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def _legacy_snapshot(connection):
    # Compare every pre-existing field, including token bytes and timestamps,
    # while permitting this migration's two new columns to appear/disappear.
    queries = {
        "galleries": "SELECT to_jsonb(g) - 'authorization_epoch' FROM galleries g ORDER BY g.id",
        "share_sessions": "SELECT to_jsonb(s) - 'authorization_epoch' FROM share_sessions s ORDER BY s.id",
        "images": "SELECT to_jsonb(i) FROM images i ORDER BY i.id",
        "selection_events": "SELECT to_jsonb(e) FROM selection_events e ORDER BY e.id",
    }
    snapshot = {}
    for table, query in queries.items():
        snapshot[table] = (await connection.execute(text(query))).scalars().all()
    return snapshot


async def test_share_authorization_epoch_backfill_defaults_and_rollback():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    owner_id = uuid.uuid4()
    gallery_ids = [uuid.uuid4(), uuid.uuid4()]
    session_ids = [uuid.uuid4(), uuid.uuid4()]
    started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    completed_at = datetime(2026, 1, 2, 4, 5, 6, tzinfo=UTC)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0010")
        # Seed the old schema with SQL: new ORM fields do not exist yet.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, status) "
                    "VALUES (:id, 'epochs@example.com', 'hash', 'active')"
                ),
                {"id": owner_id},
            )
            for index, (gallery_id, session_id) in enumerate(zip(gallery_ids, session_ids, strict=True)):
                token = f"existing-epoch-gallery-{index}"
                digest, salt = hash_token(token)
                await conn.execute(
                    text(
                        "INSERT INTO galleries (id, owner_id, name, status, share_token, share_token_hash, "
                        "share_token_salt, share_token_lookup, password_hash) "
                        "VALUES (:id, :owner, :name, :status, :raw, :digest, :salt, :locator, :password)"
                    ),
                    {
                        "id": gallery_id,
                        "owner": owner_id,
                        "name": f"Existing gallery {index}",
                        "status": "completed" if index else "shared",
                        "raw": token if index else None,
                        "digest": digest,
                        "salt": salt,
                        "locator": token_locator(token) if index else None,
                        "password": "existing-password-hash" if index else None,
                    },
                )
                image_id = uuid.uuid4()
                await conn.execute(
                    text(
                        "INSERT INTO images (id, gallery_id, storage_key, filename, processing_status) "
                        "VALUES (:id, :gallery, :key, :filename, 'ready')"
                    ),
                    {"id": image_id, "gallery": gallery_id, "key": f"old/{index}.jpg", "filename": f"{index}.jpg"},
                )
                await conn.execute(
                    text(
                        "INSERT INTO share_sessions (id, gallery_id, started_at, completed_at, ip_address, user_agent) "
                        "VALUES (:id, :gallery, :started, :completed, '192.0.2.1', 'legacy browser')"
                    ),
                    {
                        "id": session_id,
                        "gallery": gallery_id,
                        "started": started_at,
                        "completed": completed_at if index else None,
                    },
                )
                await conn.execute(
                    text(
                        "INSERT INTO selection_events (image_id, share_session_id, action, comment, created_at) "
                        "VALUES (:image, :session, :action, :comment, :created)"
                    ),
                    {
                        "image": image_id,
                        "session": session_id,
                        "action": "comment" if index else "select",
                        "comment": "Keep this historical comment" if index else None,
                        "created": started_at,
                    },
                )
            before_upgrade = await _legacy_snapshot(conn)

        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0011")
        async with engine.connect() as conn:
            assert await _legacy_snapshot(conn) == before_upgrade
            for query in (
                "SELECT authorization_epoch FROM galleries ORDER BY id",
                "SELECT authorization_epoch FROM share_sessions ORDER BY id",
            ):
                assert (await conn.execute(text(query))).scalars().all() == [0, 0]
            columns = (
                await conn.execute(
                    text(
                        "SELECT table_name, data_type, is_nullable, column_default FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name IN ('galleries', 'share_sessions') "
                        "AND column_name = 'authorization_epoch' ORDER BY table_name"
                    )
                )
            ).all()
            assert [column.table_name for column in columns] == ["galleries", "share_sessions"]
            assert all(column.data_type == "bigint" and column.is_nullable == "NO" for column in columns)
            assert all(column.column_default is not None for column in columns)

        for query, identity in (
            ("UPDATE galleries SET authorization_epoch = NULL WHERE id = :id", gallery_ids[0]),
            ("UPDATE share_sessions SET authorization_epoch = NULL WHERE id = :id", session_ids[0]),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(text(query), {"id": identity})

        new_gallery, new_session = uuid.uuid4(), uuid.uuid4()
        async with engine.begin() as conn:
            # SQL inserts that omit the new fields must receive DB-side defaults.
            assert (
                await conn.scalar(
                    text(
                        "INSERT INTO galleries (id, owner_id, name) VALUES (:id, :owner, 'New gallery') "
                        "RETURNING authorization_epoch"
                    ),
                    {"id": new_gallery, "owner": owner_id},
                )
                == 0
            )
            assert (
                await conn.scalar(
                    text(
                        "INSERT INTO share_sessions (id, gallery_id) VALUES (:id, :gallery) "
                        "RETURNING authorization_epoch"
                    ),
                    {"id": new_session, "gallery": new_gallery},
                )
                == 0
            )
            # A 64-bit generation is not silently narrowed to a 32-bit integer.
            for query, identity in (
                (
                    "UPDATE galleries SET authorization_epoch = :epoch WHERE id = :id RETURNING authorization_epoch",
                    gallery_ids[0],
                ),
                (
                    "UPDATE share_sessions SET authorization_epoch = :epoch "
                    "WHERE id = :id RETURNING authorization_epoch",
                    session_ids[0],
                ),
            ):
                assert await conn.scalar(text(query), {"epoch": 2**40, "id": identity}) == 2**40
            before_downgrade = await _legacy_snapshot(conn)

        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0010", downgrade=True)
        async with engine.connect() as conn:
            assert await _legacy_snapshot(conn) == before_downgrade
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name IN ('galleries', 'share_sessions') AND column_name = 'authorization_epoch'"
                    )
                )
                == 0
            )

        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0011")
        async with engine.connect() as conn:
            assert await _legacy_snapshot(conn) == before_downgrade
            assert await conn.scalar(text("SELECT count(*) FROM galleries WHERE authorization_epoch <> 0")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM share_sessions WHERE authorization_epoch <> 0")) == 0
    finally:
        await _drop_everything(engine)
        await engine.dispose()
