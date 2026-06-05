"""Shared gallery-deletion logic.

Extracted from the gallery-delete endpoint so it can be reused by the admin
user-delete flow (deleting a user must also purge each of their galleries,
including the physical storage files — otherwise image data is orphaned, which
is both a storage leak and a GDPR problem).
"""

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AuditLog, Gallery, Image, ImagePreview, SelectionEvent, ShareSession
from app.storage.base import StorageBackend

logger = logging.getLogger(__name__)


async def purge_gallery(gallery: Gallery, db: AsyncSession, storage: StorageBackend) -> None:
    """Delete a gallery's storage files and all dependent DB rows (no commit).

    Best-effort removes image + preview files from the storage backend, then
    deletes dependent DB records in FK-safe order and anonymises + detaches the
    gallery's audit-log entries (FK is SET NULL; done explicitly before delete).

    The caller owns the transaction: it must write any high-level audit entry
    beforehand and call ``db.commit()`` afterwards.
    """
    # 1. Delete image files from storage (best-effort: log warnings on failure)
    result = await db.execute(
        select(Image).where(Image.gallery_id == gallery.id).options(selectinload(Image.previews))
    )
    images = result.scalars().all()
    for image in images:
        try:
            await storage.delete(image.storage_key)
        except Exception:
            logger.warning("Failed to delete storage file %s during gallery deletion", image.storage_key)
        for preview in image.previews:
            try:
                await storage.delete(preview.storage_key)
            except Exception:
                logger.warning("Failed to delete preview file %s during gallery deletion", preview.storage_key)

    # 2. Delete DB records in dependency order
    await db.execute(
        delete(ImagePreview).where(ImagePreview.image_id.in_(select(Image.id).where(Image.gallery_id == gallery.id)))
    )
    await db.execute(
        delete(SelectionEvent).where(
            SelectionEvent.image_id.in_(select(Image.id).where(Image.gallery_id == gallery.id))
        )
    )
    await db.execute(delete(ShareSession).where(ShareSession.gallery_id == gallery.id))
    # Anonymize audit_log entries: keep event_type + timestamps, remove PII
    await db.execute(update(AuditLog).where(AuditLog.gallery_id == gallery.id).values(ip_address=None, user_agent=None))
    # Detach audit_log from gallery (FK is SET NULL, but we do it explicitly before delete)
    await db.execute(update(AuditLog).where(AuditLog.gallery_id == gallery.id).values(gallery_id=None))
    await db.execute(delete(Image).where(Image.gallery_id == gallery.id))
    await db.execute(delete(Gallery).where(Gallery.id == gallery.id))
