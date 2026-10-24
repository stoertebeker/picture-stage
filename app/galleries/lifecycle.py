"""Transaction fences for storage producers and deletion, across app processes.

Always acquire gallery first, then image UUIDs in order, before FK writes.
Workers/uploads share the gallery fence; whole-gallery deletion is exclusive.
Per-image exclusivity prevents duplicate workers and image-delete races while
allowing unrelated images to render in parallel. Commit/rollback releases all.
"""

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _key(namespace: bytes, identity: bytes) -> int:
    digest = hashlib.sha256(b"picture-stage:storage-lifecycle:v1\0" + namespace + identity).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def lock_gallery(db: AsyncSession, gallery_id: uuid.UUID, *, shared: bool = False) -> None:
    statement = text("SELECT pg_advisory_xact_lock_shared(:key)" if shared else "SELECT pg_advisory_xact_lock(:key)")
    # Pending audit rows must not acquire FK locks before this fence.
    with db.no_autoflush:
        await db.execute(statement, {"key": _key(b"gallery\0", gallery_id.bytes)})


async def lock_images(db: AsyncSession, gallery_id: uuid.UUID, image_ids: Iterable[uuid.UUID]) -> None:
    with db.no_autoflush:
        for image_id in sorted(set(image_ids)):
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": _key(b"image\0", gallery_id.bytes + image_id.bytes)},
            )
