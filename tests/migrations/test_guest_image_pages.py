"""0018 backfills capture keys and deterministic independent selection state."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def test_guest_page_migration_backfill_and_downgrade():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    owner, gallery, session = (uuid.uuid4() for _ in range(3))
    image_ids = [uuid.uuid4() for _ in range(5)]
    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    metadata = [
        None,
        {"DateTimeOriginal": "9999:12:31 23:59:59"},
        {"DateTimeOriginal": "bad", "DateTime": "2020:01:01 00:00:00"},
        {"DateTimeOriginal": "", "DateTime": "2020:01:01 00:00:00"},
        {"DateTimeOriginal": "2024:02:30 01:02:03"},
    ]
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0017")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users(id,email,password_hash,status) VALUES(:id,'pages@example.com','hash','active')"
                ),
                {"id": owner},
            )
            await conn.execute(
                text("INSERT INTO galleries(id,owner_id,name,status) VALUES(:id,:owner,'Historical','shared')"),
                {"id": gallery, "owner": owner},
            )
            await conn.execute(
                text("INSERT INTO share_sessions(id,gallery_id) VALUES(:id,:gallery)"),
                {"id": session, "gallery": gallery},
            )
            for image_id, exif in zip(image_ids, metadata, strict=True):
                await conn.execute(
                    text(
                        "INSERT INTO images(id,gallery_id,filename,storage_key,exif,processing_status) "
                        "VALUES(:id,:gallery,'old.jpg','original',CAST(:exif AS json),'ready')"
                    ),
                    {"id": image_id, "gallery": gallery, "exif": json.dumps(exif)},
                )
            for action, created, comment in (
                ("select", timestamp, None),
                ("deselect", timestamp, None),
                ("select", timestamp - timedelta(seconds=1), None),
                ("favorite", timestamp, None),
                ("comment", timestamp, ""),
                ("comment", timestamp + timedelta(seconds=1), None),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO selection_events(image_id,share_session_id,action,created_at,comment) "
                        "VALUES(:image,:session,:action,:created,:comment)"
                    ),
                    {
                        "image": image_ids[0],
                        "session": session,
                        "action": action,
                        "created": created,
                        "comment": comment,
                    },
                )
            before = (
                (await conn.execute(text("SELECT to_jsonb(e) FROM selection_events e ORDER BY id"))).scalars().all()
            )
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0018")
            states = (
                await conn.execute(text("SELECT image_id,selected,favorited,comment FROM current_selections"))
            ).all()
            assert states == [(image_ids[0], False, True, "")]
            projection_before = (
                (await conn.execute(text("SELECT to_jsonb(s) FROM current_selections s ORDER BY image_id")))
                .scalars()
                .all()
            )
            keys = dict((await conn.execute(text("SELECT id,capture_sort_key FROM images"))).all())
            assert keys == dict(
                zip(
                    image_ids,
                    [
                        datetime(9999, 1, 1),
                        datetime(9999, 12, 31, 23, 59, 59),
                        datetime(9999, 1, 1),
                        datetime(2020, 1, 1),
                        datetime(9999, 1, 1),
                    ],
                    strict=True,
                )
            )
            assert (
                await conn.execute(text("SELECT to_jsonb(e) FROM selection_events e ORDER BY id"))
            ).scalars().all() == before
            # Database cascades cover bulk/admin deletion without ORM traversal.
            await conn.execute(text("DELETE FROM images WHERE id=:id"), {"id": image_ids[0]})
            assert await conn.scalar(text("SELECT count(*) FROM current_selections")) == 0
            await conn.rollback()
            await conn.run_sync(_migrate, "0017", downgrade=True)
            assert await conn.scalar(text("SELECT to_regclass('current_selections')")) is None
            assert (
                await conn.execute(text("SELECT to_jsonb(e) FROM selection_events e ORDER BY id"))
            ).scalars().all() == before
            await conn.rollback()
            await conn.run_sync(_migrate, "0018")
            assert (
                await conn.execute(text("SELECT to_jsonb(e) FROM selection_events e ORDER BY id"))
            ).scalars().all() == before
            assert (
                await conn.execute(text("SELECT to_jsonb(s) FROM current_selections s ORDER BY image_id"))
            ).scalars().all() == projection_before
            assert dict((await conn.execute(text("SELECT id,capture_sort_key FROM images"))).all()) == keys
    finally:
        await _drop_everything(engine)
        await engine.dispose()
