"""0014 -> 0015 preserves old grants and history; only new visits get TTLs."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def snapshot(connection):
    tables = ["users", "galleries", "images", "selection_events", "audit_log", "revoked_tokens"]
    result = {}
    for table in tables:
        result[table] = (await connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t"))).scalars().all()  # noqa: S608
    result["share_sessions"] = (
        (await connection.execute(text("SELECT to_jsonb(t) - 'expires_at' FROM share_sessions t ORDER BY id")))
        .scalars()
        .all()
    )
    return result


async def test_expiry_migration_preserves_null_grants_data_and_downgrade():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    owner, gallery, image = (uuid.uuid4() for _ in range(3))
    sessions = [uuid.uuid4(), uuid.uuid4()]
    timestamp = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0014")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id,email,password_hash,status) "
                    "VALUES (:id,'retention@example.com','hash','active')"
                ),
                {"id": owner},
            )
            await conn.execute(
                text(
                    "INSERT INTO galleries (id,owner_id,name,status,authorization_epoch,"
                    "share_token_hash,share_token_salt) "
                    "VALUES (:id,:owner,'Existing','completed',1099511627776,:hash,:salt)"
                ),
                {"id": gallery, "owner": owner, "hash": b"keep hash", "salt": b"keep salt"},
            )
            await conn.execute(
                text(
                    "INSERT INTO images (id,gallery_id,filename,storage_key,processing_status) "
                    "VALUES (:id,:gallery,'keep.jpg','keep.jpg','ready')"
                ),
                {"id": image, "gallery": gallery},
            )
            for index, identity in enumerate(sessions):
                await conn.execute(
                    text(
                        "INSERT INTO share_sessions (id,gallery_id,authorization_epoch,started_at,completed_at,"
                        "ip_address,user_agent) VALUES (:id,:gallery,:epoch,:started,:completed,'192.0.2.1','legacy')"
                    ),
                    {
                        "id": identity,
                        "gallery": gallery,
                        "epoch": 2**40 + index,
                        "started": timestamp,
                        "completed": timestamp if index else None,
                    },
                )
                await conn.execute(
                    text(
                        "INSERT INTO selection_events (image_id,share_session_id,action,comment,created_at) "
                        "VALUES (:image,:session,'comment','Keep history',:created)"
                    ),
                    {"image": image, "session": identity, "created": timestamp},
                )
                await conn.execute(
                    text(
                        "INSERT INTO audit_log (gallery_id,event_type,actor_session_id,created_at) "
                        "VALUES (:gallery,'guest',:session,:created)"
                    ),
                    {"gallery": gallery, "session": identity, "created": timestamp},
                )
            await conn.execute(
                text("INSERT INTO revoked_tokens VALUES (:fingerprint,:expires)"),
                {"fingerprint": "a" * 64, "expires": timestamp},
            )
            before = await snapshot(conn)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0015")
            assert await snapshot(conn) == before
            assert (await conn.execute(text("SELECT expires_at FROM share_sessions"))).scalars().all() == [None, None]
            column = (
                await conn.execute(
                    text(
                        "SELECT data_type,is_nullable,column_default FROM information_schema.columns "
                        "WHERE table_name='share_sessions' AND column_name='expires_at'"
                    )
                )
            ).one()
            assert tuple(column) == ("timestamp with time zone", "YES", None)
            assert await conn.scalar(text("SELECT count(*) FROM share_session_cleanup_state")) == 0
            for name in ("ix_share_sessions_gallery_expiry", "ix_audit_log_actor_session_id"):
                assert await conn.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname=:name"), {"name": name})
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO share_session_cleanup_state (gallery_id,last_expires_at,last_session_id) "
                    "VALUES (:gallery,:expires,:session)"
                ),
                {"gallery": gallery, "expires": timestamp, "session": sessions[0]},
            )
            checkpoint = (
                await conn.execute(
                    text(
                        "SELECT last_expires_at,last_session_id FROM share_session_cleanup_state "
                        "WHERE gallery_id=:gallery"
                    ),
                    {"gallery": gallery},
                )
            ).one()
            assert tuple(checkpoint) == (timestamp, sessions[0])
            temporary_gallery = uuid.uuid4()
            await conn.execute(
                text("INSERT INTO galleries (id,owner_id,name) VALUES (:id,:owner,'Temporary')"),
                {"id": temporary_gallery, "owner": owner},
            )
            await conn.execute(
                text("INSERT INTO share_session_cleanup_state (gallery_id) VALUES (:gallery)"),
                {"gallery": temporary_gallery},
            )
            await conn.execute(text("DELETE FROM galleries WHERE id=:id"), {"id": temporary_gallery})
            assert await conn.scalar(text("SELECT count(*) FROM share_session_cleanup_state")) == 1
            assert (
                await conn.scalar(
                    text(
                        "INSERT INTO share_sessions (id,gallery_id,expires_at) "
                        "VALUES (:id,:gallery,:expires) RETURNING expires_at"
                    ),
                    {"id": uuid.uuid4(), "gallery": gallery, "expires": timestamp},
                )
                == timestamp
            )
            assert (
                await conn.scalar(
                    text("INSERT INTO share_sessions (id,gallery_id) VALUES (:id,:gallery) RETURNING expires_at"),
                    {"id": uuid.uuid4(), "gallery": gallery},
                )
                is None
            )
            before_downgrade = await snapshot(conn)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0014", downgrade=True)
            assert await snapshot(conn) == before_downgrade
            assert await conn.scalar(text("SELECT to_regclass('share_session_cleanup_state')")) is None
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_name='share_sessions' "
                        "AND column_name='expires_at'"
                    )
                )
                == 0
            )
            await conn.run_sync(_migrate, "0015")
            assert await snapshot(conn) == before_downgrade
            assert await conn.scalar(text("SELECT count(*) FROM share_sessions WHERE expires_at IS NOT NULL")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM share_session_cleanup_state")) == 0
    finally:
        await _drop_everything(engine)
        await engine.dispose()
