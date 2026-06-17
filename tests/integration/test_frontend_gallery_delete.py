"""Integration tests for the frontend gallery-delete endpoint (picture-stage-bkw 2.2).

The HTMX delete (`POST /galleries/{id}/delete`) now routes through the shared
``purge_gallery`` helper (same as the JSON API), so a deleted gallery's storage
files are removed — no orphaned image/preview files (a storage leak + GDPR
problem). These tests assert the storage backend is asked to delete every file
key, and that a name-mismatch deletes nothing.
"""

from sqlalchemy import select

from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus, Image, ImagePreview, PreviewVariant
from app.main import app
from app.storage.dependencies import get_storage


class _RecordingStorage:
    """Minimal storage stub recording deleted keys (duck-typed for purge_gallery)."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def _gallery_with_files(db, owner) -> Gallery:
    gallery = Gallery(owner_id=owner.id, name="Zu löschen", status=GalleryStatus.draft)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    img = Image(
        gallery_id=gallery.id,
        storage_key="proofs/orig.jpg",
        filename="orig.jpg",
        content_type="image/jpeg",
        sort_order=0,
    )
    db.add(img)
    await db.commit()
    await db.refresh(img)

    db.add(
        ImagePreview(
            image_id=img.id,
            variant=PreviewVariant.thumb_sm,
            storage_key="proofs/orig_thumb_sm.webp",
            width=320,
            height=240,
            file_size=1234,
        )
    )
    await db.commit()
    return gallery


async def test_frontend_delete_purges_storage_files(client, db, owner, verify_db):
    gallery = await _gallery_with_files(db, owner)

    fake = _RecordingStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    client.cookies.set("session", create_access_token(str(owner.id)))
    client.cookies.set("csrf_token", "test-csrf")
    try:
        resp = await client.post(
            f"/galleries/{gallery.id}/delete",
            data={"confirm_name": "Zu löschen"},
            headers={"X-CSRF-Token": "test-csrf"},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    assert resp.status_code == 303
    # Both the original and the preview file were purged — no orphans left behind.
    assert set(fake.deleted) == {"proofs/orig.jpg", "proofs/orig_thumb_sm.webp"}
    # The gallery row is gone.
    found = (await verify_db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none()
    assert found is None


async def test_frontend_delete_name_mismatch_keeps_gallery_and_files(client, db, owner, verify_db):
    gallery = await _gallery_with_files(db, owner)

    fake = _RecordingStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    client.cookies.set("session", create_access_token(str(owner.id)))
    client.cookies.set("csrf_token", "test-csrf")
    try:
        resp = await client.post(
            f"/galleries/{gallery.id}/delete",
            data={"confirm_name": "falscher Name"},
            headers={"X-CSRF-Token": "test-csrf"},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    # Confirmation gate: re-renders the detail page, deletes nothing.
    assert resp.status_code == 200
    assert fake.deleted == []
    found = (await verify_db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none()
    assert found is not None
