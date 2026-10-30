"""Atomic image batches with durable storage cleanup ownership.

An intent is committed before storage writes. Image rows still commit together;
failed cleanup keeps the intent for a later gallery/user deletion retry.
"""

import logging
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import BinaryIO

from anyio import CancelScope
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.db.models import Image, UploadCleanupIntent
from app.galleries.lifecycle import lock_gallery
from app.images.storage_cleanup import canonical_preview_keys, delete_storage_keys
from app.storage.base import StorageBackend, storage_key
from app.storage.uploads import complete_upload

logger = logging.getLogger(__name__)

_ORIGINAL_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@dataclass(frozen=True)
class UploadPlan:
    image_id: uuid.UUID
    original_key: str
    extension: str
    keys: frozenset[str]

    @classmethod
    def for_file(cls, gallery_id: uuid.UUID, content_type: str | None, *, previews: bool) -> "UploadPlan":
        # Filenames remain unchanged as image metadata, but never become part of
        # a key needed for cleanup (paths, NULs and long suffixes are unsafe).
        extension = _ORIGINAL_EXTENSIONS.get(content_type or "")
        if extension is None:
            raise ValueError("Unsupported upload content type")
        image_id = uuid.uuid4()
        original = storage_key(str(gallery_id), "originals", f"{image_id}.{extension}")
        keys = {original}
        if previews:
            keys.update(canonical_preview_keys(gallery_id, image_id))
        return cls(image_id, original, extension, frozenset(keys))


class UploadBatch:
    def __init__(
        self, db: AsyncSession, storage: StorageBackend, gallery_id: uuid.UUID, plans: list[UploadPlan]
    ) -> None:
        self.db = db
        self.storage = storage
        self.gallery_id = gallery_id
        self.plans = plans
        self.intent_id = uuid.uuid4()
        self.attempted_keys: set[str] = set()
        self._owned_keys = {key for plan in plans for key in plan.keys}
        self._committed = False
        bind = db.bind
        if bind is None:
            raise RuntimeError("Upload recovery requires a bound database session")
        # Recovery must use an independent connection to the request's actual
        # database, including dependency-overridden integration test engines.
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def __aenter__(self) -> "UploadBatch":
        self.db.add(
            UploadCleanupIntent(
                id=self.intent_id,
                gallery_id=self.gallery_id,
                image_keys={str(plan.image_id): sorted(plan.keys) for plan in self.plans},
            )
        )
        await self.db.commit()
        # The caller must now reselect gallery + ownership before any writes:
        # deletion may have won the gap after the intent commit released locks.
        await lock_gallery(self.db, self.gallery_id, shared=True)
        return self

    async def upload(self, key: str, data: BinaryIO, content_type: str) -> str:
        if key not in self._owned_keys:
            raise ValueError("Storage key is not owned by this upload batch")
        # Register before awaiting a write that may fail after storing bytes.
        self.attempted_keys.add(key)
        return await complete_upload(self.storage, key, data, content_type)

    async def commit(self) -> None:
        # Removing the intent is atomic with the complete image/preview batch.
        # A lost commit acknowledgement is distinguished in a fresh session.
        await self.db.execute(delete(UploadCleanupIntent).where(UploadCleanupIntent.id == self.intent_id))
        await self.db.commit()
        self._committed = True

    async def _recover(self) -> None:
        try:
            await self.db.rollback()
        except Exception:
            # A broken request connection must not prevent an independent
            # recovery attempt. The already committed intent remains durable.
            logger.exception("Could not roll back failed upload batch %s", self.intent_id)
            try:
                await self.db.invalidate()
            except Exception:
                logger.exception("Could not invalidate failed upload connection")
        try:
            async with self._sessions() as db:
                # One exclusive gallery fence protects every image in the batch
                # from workers/deletes, without allocating one lock per input.
                await lock_gallery(db, self.gallery_id)
                ids = [plan.image_id for plan in self.plans]
                committed = set(
                    (
                        await db.scalars(select(Image.id).where(Image.gallery_id == self.gallery_id, Image.id.in_(ids)))
                    ).all()
                )
                removable = {
                    key
                    for plan in self.plans
                    if plan.image_id not in committed
                    for key in plan.keys
                    if key in self.attempted_keys
                }
                await delete_storage_keys(self.storage, removable)
                await db.execute(delete(UploadCleanupIntent).where(UploadCleanupIntent.id == self.intent_id))
                await db.commit()
        except Exception:
            # Never guess after an unreadable commit outcome, or lose keys when
            # storage cleanup fails. The intent remains for explicit deletion.
            logger.exception("Upload cleanup pending for batch %s; keeping durable intent", self.intent_id)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            # ASGI middleware uses AnyIO level cancellation. Keep cleanup in the
            # request task and shield its cancel scope, otherwise dependency
            # teardown can close the same Session while a shielded child rolls
            # it back. Propagation of the original exception is unchanged.
            with CancelScope(shield=True):
                await self._recover()
