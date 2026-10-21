"""Database-backed HTML contracts for upload preview progress (GitHub issue #3)."""

import base64

from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus, Image, ImageProcessingStatus
from app.main import app
from app.storage.dependencies import get_storage


class _RecordingUploadStorage:
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str]] = []

    async def upload(self, key, _data, content_type):
        self.uploaded.append((key, content_type))
        return key


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


async def test_gallery_get_replaces_missing_csrf_cookie_before_real_upload(client, db, owner, monkeypatch) -> None:
    """A returning session can upload directly after its CSRF cookie expired."""
    gallery = Gallery(owner_id=owner.id, name="CSRF upload", status=GalleryStatus.draft)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    storage = _RecordingUploadStorage()

    async def skip_preview_worker(*_args) -> None:
        pass

    monkeypatch.setattr("app.images.preview_worker.process_image_previews", skip_preview_worker)
    app.dependency_overrides[get_storage] = lambda: storage
    client.cookies.set("session", create_access_token(str(owner.id)))
    try:
        page = await client.get(f"/galleries/{gallery.id}")
        token = page.cookies.get("csrf_token")
        assert page.status_code == 200
        assert token
        assert f'hx-headers=\'{{"X-CSRF-Token": "{token}"}}\'' in page.text
        assert f'name="csrf_token" value="{token}"' in page.text

        # One-pixel PNG. The multipart POST exercises CSRF, image validation,
        # persistence and the pending grid response; only the async preview
        # worker is stubbed to keep this integration test deterministic.
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        uploaded = await client.post(
            f"/galleries/{gallery.id}/upload",
            headers={"X-CSRF-Token": token},
            files={"files": ("csrf-regression.png", png, "image/png")},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    assert uploaded.status_code == 200
    assert storage.uploaded and storage.uploaded[0][1] == "image/png"
    assert 'data-processing-status="pending"' in uploaded.text
