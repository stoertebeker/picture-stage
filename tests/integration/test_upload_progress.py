"""Database-backed HTML contracts for upload preview progress (GitHub issue #3)."""

from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus, Image, ImageProcessingStatus


async def test_owner_grid_polls_until_every_image_is_terminal(client, db, owner) -> None:
    gallery = Gallery(owner_id=owner.id, name="Progress", status=GalleryStatus.draft)
    db.add(gallery)
    await db.flush()

    first = Image(
        gallery_id=gallery.id,
        storage_key="progress/first.jpg",
        filename="first.jpg",
        content_type="image/jpeg",
        sort_order=0,
        processing_status=ImageProcessingStatus.pending,
    )
    second = Image(
        gallery_id=gallery.id,
        storage_key="progress/second.jpg",
        filename="second.jpg",
        content_type="image/jpeg",
        sort_order=1,
        processing_status=ImageProcessingStatus.pending,
    )
    db.add_all((first, second))
    await db.commit()

    client.cookies.set("session", create_access_token(str(owner.id)))
    endpoint = f"/galleries/{gallery.id}/images-grid"

    pending = await client.get(endpoint)
    assert pending.status_code == 200
    assert f'data-image-id="{first.id}"' in pending.text
    assert 'data-processing-status="pending"' in pending.text
    assert 'hx-trigger="every 2s"' in pending.text

    first.processing_status = ImageProcessingStatus.ready
    await db.commit()
    mixed = await client.get(endpoint)
    assert mixed.status_code == 200
    assert 'data-processing-status="ready"' in mixed.text
    assert 'data-processing-status="pending"' in mixed.text
    assert 'hx-trigger="every 2s"' in mixed.text

    second.processing_status = ImageProcessingStatus.failed
    await db.commit()
    terminal = await client.get(endpoint)
    assert terminal.status_code == 200
    assert 'data-processing-status="ready"' in terminal.text
    assert 'data-processing-status="failed"' in terminal.text
    assert 'hx-trigger="every 2s"' not in terminal.text
