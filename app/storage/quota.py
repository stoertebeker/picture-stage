"""Durable per-owner admission shared by uploads, workers and copies.

Never acquire lifecycle fences in the short accounting transactions. Callers
already hold gallery/image fences; accounting commits before I/O and is refunded
only after confirmed physical deletion. A parent rollback cannot lose a charge.
"""

import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import BinaryIO

from anyio import CancelScope
from sqlalchemy import String, any_, bindparam, delete, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models import StorageAllocation, StorageUsage
from app.storage.base import StorageBackend
from app.storage.uploads import complete_upload


class StorageQuotaExceeded(Exception):
    """No additional durable storage may be allocated for this owner."""


@asynccontextmanager
async def accounting_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    bind = db.bind
    if bind is None:
        raise RuntimeError("Storage accounting requires a bound session")
    parent_engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    # Every caller already holds a connection while retaining lifecycle fences.
    # Borrowing its pool again deadlocks when all parent slots are occupied.
    # A short-lived NullPool engine also avoids pooled connections crossing test
    # event loops. Pass the URL object: str(URL) masks database passwords.
    engine = create_async_engine(parent_engine.url, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        with CancelScope(shield=True):
            try:
                await session.close()
            finally:
                await engine.dispose()


async def reserve(
    db: AsyncSession, owner_id: uuid.UUID, gallery_id: uuid.UUID, image_id: uuid.UUID, key: str, size: int
) -> None:
    if size < 0:
        raise ValueError("Negative storage size")
    async with accounting_session(db) as accounting:
        await accounting.execute(insert(StorageUsage).values(owner_id=owner_id).on_conflict_do_nothing())
        # Serialize same-owner keys, including retry/overwrite admission. No
        # user row UPDATE lock, which would deadlock gallery-slot reservations.
        await accounting.execute(
            select(StorageUsage.owner_id).where(StorageUsage.owner_id == owner_id).with_for_update()
        )
        previous = await accounting.get(StorageAllocation, key)
        if previous is not None and (
            previous.owner_id != owner_id or previous.gallery_id != gallery_id or previous.image_id != image_id
        ):
            raise ValueError("Storage allocation ownership mismatch")
        if previous is not None and previous.size is None:
            raise StorageQuotaExceeded()
        extra_bytes = max(0, size - previous.size) if previous is not None and previous.size is not None else size
        extra_objects = int(previous is None)
        accepted = await accounting.scalar(
            update(StorageUsage)
            .where(
                StorageUsage.owner_id == owner_id,
                StorageUsage.unknown_sizes == 0,
                StorageUsage.bytes <= settings.max_storage_bytes_per_owner - extra_bytes,
                StorageUsage.objects <= settings.max_storage_objects_per_owner - extra_objects,
            )
            .values(bytes=StorageUsage.bytes + extra_bytes, objects=StorageUsage.objects + extra_objects)
            .returning(StorageUsage.owner_id)
        )
        if accepted is None:
            raise StorageQuotaExceeded()
        if previous is None:
            accounting.add(
                StorageAllocation(key=key, owner_id=owner_id, gallery_id=gallery_id, image_id=image_id, size=size)
            )
        else:
            previous.size = max(previous.size or 0, size)
        await accounting.commit()


async def release(db: AsyncSession, keys: Iterable[str]) -> None:
    """Idempotently refund only keys whose physical deletion has succeeded."""
    keys = sorted(set(keys))
    if not keys:
        return
    # The ordinary 40,000-object allowance exceeds asyncpg's 32,767 scalar
    # parameter ceiling. Bind the key set once as an array for every query.
    key_filter = StorageAllocation.key == any_(bindparam("quota_release_keys", keys, type_=ARRAY(String(1024))))
    async with accounting_session(db) as accounting:
        owners = sorted(set((await accounting.scalars(select(StorageAllocation.owner_id).where(key_filter))).all()))
        for owner_id in owners:
            await accounting.execute(
                select(StorageUsage.owner_id).where(StorageUsage.owner_id == owner_id).with_for_update()
            )
            allocations = (
                await accounting.scalars(
                    select(StorageAllocation).where(StorageAllocation.owner_id == owner_id, key_filter)
                )
            ).all()
            await accounting.execute(
                update(StorageUsage)
                .where(StorageUsage.owner_id == owner_id)
                .values(
                    bytes=StorageUsage.bytes - sum(item.size or 0 for item in allocations),
                    objects=StorageUsage.objects - len(allocations),
                    unknown_sizes=StorageUsage.unknown_sizes - sum(item.size is None for item in allocations),
                )
            )
            await accounting.execute(
                delete(StorageAllocation).where(StorageAllocation.owner_id == owner_id, key_filter)
            )
        await accounting.commit()


async def allocation_keys(
    db: AsyncSession,
    *,
    gallery_id: uuid.UUID | None = None,
    image_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
) -> set[str]:
    query = select(StorageAllocation.key)
    if gallery_id is not None:
        query = query.where(StorageAllocation.gallery_id == gallery_id)
    if image_id is not None:
        query = query.where(StorageAllocation.image_id == image_id)
    if owner_id is not None:
        query = query.where(StorageAllocation.owner_id == owner_id)
    return set((await db.scalars(query)).all())


async def upload(
    db: AsyncSession,
    storage: StorageBackend,
    owner_id: uuid.UUID,
    gallery_id: uuid.UUID,
    image_id: uuid.UUID,
    key: str,
    data: BinaryIO,
    content_type: str,
) -> str:
    position = data.tell()
    data.seek(0, 2)
    size = data.tell() - position
    data.seek(position)
    await reserve(db, owner_id, gallery_id, image_id, key, size)
    return await complete_upload(storage, key, data, content_type)
