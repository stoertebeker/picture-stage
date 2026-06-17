"""Background worker that generates preview variants for an uploaded image.

Preview generation (Pillow resize + watermark + WebP encode) is CPU-bound and
blocking. Running it inline in the upload request froze the UI and blocked the
whole event loop. This worker is dispatched via FastAPI BackgroundTasks: it opens
its own DB session, reads the stored original, generates all variants in a
process pool with a hard per-file timeout (``run_in_pool``, picture-stage-q9td;
keeps the event loop free and can kill a hung variant), and flips
``images.processing_status`` to ``ready`` (or ``failed`` on error).
"""

import io
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Image, ImagePreview, ImageProcessingStatus, PreviewVariant
from app.images.process_pool import run_in_pool
from app.images.processing import (
    PREVIEW_SIZES,
    render_preview_bytes,
    render_thumbnail_bytes,
)
from app.storage.base import storage_key
from app.storage.dependencies import get_storage

logger = logging.getLogger(__name__)


async def _read_original(storage_key_str: str) -> bytes:
    """Read the stored original back into memory for processing."""
    storage = get_storage()
    chunks: list[bytes] = []
    async for chunk in storage.download_stream(storage_key_str):
        chunks.append(chunk)
    return b"".join(chunks)


async def _mark_failed(image_id: uuid.UUID, gallery_id: uuid.UUID) -> None:
    """Set processing_status=failed in a fresh transaction.

    Runs in its own session so the status write survives even if the main
    processing transaction was rolled back.
    """
    try:
        async with async_session() as db:
            result = await db.execute(select(Image).where(Image.id == image_id, Image.gallery_id == gallery_id))
            image = result.scalar_one_or_none()
            if image is not None:
                image.processing_status = ImageProcessingStatus.failed
                await db.commit()
    except Exception:
        # Last-resort: the failure handler must never raise.
        logger.exception("Failed to mark image %s as failed", image_id)


async def process_image_previews(
    image_id: uuid.UUID,
    gallery_id: uuid.UUID,
    watermark_config: dict[str, Any] | None = None,
) -> None:
    """Generate all preview variants for one image and update its status.

    ``watermark_config`` is the owning gallery's per-gallery watermark settings
    (text/position/opacity/font_size/enabled); ``None`` or an empty dict falls
    back to the global defaults, and ``enabled: false`` skips the overlay.

    Tenant isolation: the image is loaded by (image_id, gallery_id) so a worker
    can never touch an image outside its gallery. On success the status becomes
    ``ready``; any exception flips it to ``failed`` (in a separate transaction).
    """
    storage = get_storage()
    try:
        async with async_session() as db:
            result = await db.execute(select(Image).where(Image.id == image_id, Image.gallery_id == gallery_id))
            image = result.scalar_one_or_none()
            if image is None:
                logger.warning(
                    "Preview worker: image %s not found in gallery %s (deleted?)",
                    image_id,
                    gallery_id,
                )
                return

            original_bytes = await _read_original(image.storage_key)

            for variant_name, max_width in PREVIEW_SIZES.items():
                # Run the CPU-bound Pillow work in the process pool so a hung
                # variant can be hard-killed by the per-file timeout (q9td).
                if variant_name == "preview":
                    preview_bytes, pw, ph = await run_in_pool(
                        render_preview_bytes,
                        original_bytes,
                        max_width,
                        watermark_config,
                        str(gallery_id),
                    )
                else:
                    preview_bytes, pw, ph = await run_in_pool(render_thumbnail_bytes, original_bytes, max_width)

                preview_key = storage_key(str(gallery_id), "previews", f"{image_id}_{variant_name}.webp")
                await storage.upload(preview_key, io.BytesIO(preview_bytes), "image/webp")

                db.add(
                    ImagePreview(
                        image_id=image_id,
                        variant=PreviewVariant(variant_name),
                        storage_key=preview_key,
                        width=pw,
                        height=ph,
                        file_size=len(preview_bytes),
                    )
                )

            image.processing_status = ImageProcessingStatus.ready
            await db.commit()
            logger.info("Preview worker: image %s ready (%d variants)", image_id, len(PREVIEW_SIZES))
    except Exception:
        logger.exception("Preview worker failed for image %s", image_id)
        await _mark_failed(image_id, gallery_id)
