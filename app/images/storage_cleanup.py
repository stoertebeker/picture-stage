"""Recoverable image-file cleanup shared by workers and deletion routes."""

import logging
import uuid
from collections.abc import Iterable

from app.db.models import Image, PreviewVariant
from app.storage.base import StorageBackend, storage_key

logger = logging.getLogger(__name__)


class StorageCleanupError(RuntimeError):
    """Some keys could not be removed; keep their durable DB references."""

    def __init__(self, failed_keys: Iterable[str]) -> None:
        self.failed_keys = tuple(sorted(set(failed_keys)))
        super().__init__("Storage cleanup incomplete; retry required")


def canonical_preview_keys(gallery_id: uuid.UUID, image_id: uuid.UUID) -> set[str]:
    # These names are recoverable even when processing rolled back its preview
    # rows, or upload raised after writing bytes to the final object key.
    return {storage_key(str(gallery_id), "previews", f"{image_id}_{variant.value}.webp") for variant in PreviewVariant}


def image_storage_keys(image: Image) -> set[str]:
    return (
        {image.storage_key}
        | {preview.storage_key for preview in image.previews}
        | canonical_preview_keys(image.gallery_id, image.id)
    )


async def delete_storage_keys(storage: StorageBackend, keys: Iterable[str]) -> None:
    failed = []
    for key in sorted(set(keys)):
        try:
            await storage.delete(key)
        except Exception:
            logger.exception("Could not remove storage key %s; retaining cleanup references", key)
            failed.append(key)
    if failed:
        raise StorageCleanupError(failed)
