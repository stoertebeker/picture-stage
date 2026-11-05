"""Backfill legacy storage without deleting data or inventing zero sizes."""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from tests.migrations.test_bootstrap_state_migration import _migrate
from tests.migrations.test_migration_drift import _drop_everything


async def _snapshot(conn):
    result = {}
    queries = {
        "users": "SELECT to_jsonb(r) FROM users r ORDER BY id",
        "galleries": "SELECT to_jsonb(r) FROM galleries r ORDER BY id",
        "images": "SELECT to_jsonb(r) FROM images r ORDER BY id",
        "image_previews": "SELECT to_jsonb(r) FROM image_previews r ORDER BY id",
        "upload_cleanup_intents": "SELECT to_jsonb(r) FROM upload_cleanup_intents r ORDER BY id",
    }
    for table, query in queries.items():
        result[table] = (await conn.execute(text(query))).scalars().all()
    return result


async def test_storage_quota_backfills_exact_known_and_blocks_unknown_sizes():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    gallery, other_gallery = uuid.uuid4(), uuid.uuid4()
    ready, pending, failed, other_ready = (uuid.uuid4() for _ in range(4))
    ready_key = f"{gallery}/originals/{ready}.png"
    unknown_key = f"{gallery}/originals/{uuid.uuid4()}.png"
    try:
        await _drop_everything(engine)
        async with engine.connect() as conn:
            await conn.run_sync(_migrate, "0012")
        async with engine.begin() as conn:
            for user_id, email in ((owner, "known@example.test"), (other_owner, "other@example.test")):
                await conn.execute(
                    text(
                        "INSERT INTO users (id,email,password_hash,status) "
                        "VALUES (:id,:email,'unchanged-hash','active')"
                    ),
                    {"id": user_id, "email": email},
                )
            for gallery_id, user_id in ((gallery, owner), (other_gallery, other_owner)):
                await conn.execute(
                    text(
                        "INSERT INTO galleries (id,owner_id,name,share_token,share_token_hash,share_token_salt) "
                        "VALUES (:id,:owner,'Legacy','unchanged-token',:hash,:salt)"
                    ),
                    {"id": gallery_id, "owner": user_id, "hash": b"unchanged-hash", "salt": b"unchanged-salt"},
                )
            for image_id, gallery_id, size, status in (
                (ready, gallery, 2_000_000_000, "ready"),
                (pending, gallery, None, "pending"),
                (failed, gallery, -1, "failed"),
                (other_ready, other_gallery, 17, "ready"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO images (id,gallery_id,storage_key,filename,file_size,processing_status) "
                        "VALUES (:id,:gallery,:key,'unchanged.png',:size,:status)"
                    ),
                    {
                        "id": image_id,
                        "gallery": gallery_id,
                        "key": f"{gallery_id}/originals/{image_id}.png",
                        "size": size,
                        "status": status,
                    },
                )
            for variant in ("thumb_sm", "thumb_md", "preview"):
                await conn.execute(
                    text(
                        "INSERT INTO image_previews (image_id,variant,storage_key,width,height,file_size) "
                        "VALUES (:id,:variant,:key,100,80,1000000000)"
                    ),
                    {"id": ready, "variant": variant, "key": f"{gallery}/previews/{ready}_{variant}.webp"},
                )
            await conn.execute(
                text(
                    "INSERT INTO image_previews (image_id,variant,storage_key,width,height,file_size) "
                    "VALUES (:id,'preview',:key,100,80,19)"
                ),
                {"id": other_ready, "key": f"{other_gallery}/previews/{other_ready}_preview.webp"},
            )
            # Known keys remain exact; duplicate intent keys are charged once.
            for _ in range(2):
                await conn.execute(
                    text(
                        "INSERT INTO upload_cleanup_intents (id,gallery_id,image_keys) "
                        "VALUES (:id,:gallery,CAST(:keys AS json))"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "gallery": gallery,
                        "keys": json.dumps(
                            {
                                str(ready): [ready_key],
                                str(pending): [f"{gallery}/previews/{pending}_preview.webp"],
                                str(uuid.uuid4()): [unknown_key],
                            }
                        ),
                    },
                )
            before = await _snapshot(conn)
        for reupgrade in (False, True):
            async with engine.connect() as conn:
                if reupgrade:
                    await conn.run_sync(_migrate, "0012", downgrade=True)
                    assert await _snapshot(conn) == before
                    await conn.commit()
                await conn.run_sync(_migrate, "0013")
            async with engine.connect() as conn:
                assert await _snapshot(conn) == before
                usage = (
                    await conn.execute(
                        text("SELECT owner_id, bytes, objects, unknown_sizes FROM storage_usage ORDER BY owner_id")
                    )
                ).all()
                assert {row.owner_id: tuple(row[1:]) for row in usage} == {
                    owner: (5_000_000_000, 13, 9),
                    other_owner: (36, 2, 0),
                }
                assert (
                    await conn.scalar(text("SELECT size FROM storage_allocations WHERE key=:key"), {"key": ready_key})
                    == 2_000_000_000
                )
                assert (
                    await conn.scalar(
                        text("SELECT COUNT(*) FROM storage_allocations WHERE owner_id=:owner"), {"owner": owner}
                    )
                    == 13
                )
                assert await conn.scalar(
                    text("SELECT size IS NULL FROM storage_allocations WHERE key=:key"), {"key": unknown_key}
                )
    finally:
        await _drop_everything(engine)
        await engine.dispose()
