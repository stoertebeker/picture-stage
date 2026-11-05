"""Serialize owner credential changes without deleting historical sessions."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Gallery
from app.galleries.lifecycle import lock_gallery


async def lock_owned_gallery_authorization(gallery_id: uuid.UUID, owner_id: uuid.UUID, db: AsyncSession) -> Gallery:
    """Take lifecycle then row locks and refresh before incrementing the epoch.

    Call after password hashing, before assigning any credential fields. The
    caller advances the epoch once and commits it with the credential changes.
    """
    await lock_gallery(db, gallery_id, shared=True)
    with db.no_autoflush:
        locked_id = await db.scalar(
            select(Gallery.id).where(Gallery.id == gallery_id, Gallery.owner_id == owner_id).with_for_update()
        )
        gallery: Gallery | None = None
        if locked_id is not None:
            gallery = await db.scalar(
                select(Gallery)
                .where(Gallery.id == gallery_id, Gallery.owner_id == owner_id)
                .execution_options(populate_existing=True)
            )
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")
    return gallery
