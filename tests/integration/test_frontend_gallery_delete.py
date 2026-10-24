"""Integration tests for the frontend gallery-delete endpoint (picture-stage-bkw 2.2).

The HTMX delete (`POST /galleries/{id}/delete`) now routes through the shared
``purge_gallery`` helper (same as the JSON API), so a deleted gallery's storage
files are removed — no orphaned image/preview files (a storage leak + GDPR
problem). These tests assert the storage backend is asked to delete every file
key, and that an unconfirmed request deletes nothing.
"""

import pytest
from sqlalchemy import select

from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus, Image, ImagePreview, PreviewVariant
from app.main import app
from app.security.middleware import _mint_csrf_token
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
    session_token = create_access_token(str(owner.id))
    csrf_token = _mint_csrf_token(session_token)
    client.cookies.set("session", session_token)
    client.cookies.set("csrf_token", csrf_token)
    try:
        dashboard = await client.get("/dashboard")
        assert dashboard.status_code == 200
        assert f'data-open-dialog="deleteModal-{gallery.id}"' in dashboard.text
        assert f'action="/galleries/{gallery.id}/delete"' in dashboard.text
        resp = await client.post(
            f"/galleries/{gallery.id}/delete",
            data={"confirm_delete": "yes", "csrf_token": csrf_token},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    # Both the original and the preview file were purged — no orphans left behind.
    assert set(fake.deleted) == {"proofs/orig.jpg", "proofs/orig_thumb_sm.webp"}
    # The gallery row is gone.
    found = (await verify_db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none()
    assert found is None


@pytest.mark.parametrize("confirmation", [None, "no"])
async def test_frontend_delete_without_confirmation_keeps_gallery_and_files(client, db, owner, verify_db, confirmation):
    gallery = await _gallery_with_files(db, owner)

    fake = _RecordingStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    session_token = create_access_token(str(owner.id))
    csrf_token = _mint_csrf_token(session_token)
    client.cookies.set("session", session_token)
    client.cookies.set("csrf_token", csrf_token)
    try:
        resp = await client.post(
            f"/galleries/{gallery.id}/delete",
            data={} if confirmation is None else {"confirm_delete": confirmation},
            headers={"X-CSRF-Token": csrf_token},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    # Confirmation gate rejects requests that did not confirm deletion.
    assert resp.status_code == 400
    assert fake.deleted == []
    found = (await verify_db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none()
    assert found is not None


async def test_frontend_delete_rejects_other_owner(client, db, owner, other_user, verify_db):
    gallery = await _gallery_with_files(db, owner)
    fake = _RecordingStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    session_token = create_access_token(str(other_user.id))
    csrf_token = _mint_csrf_token(session_token)
    client.cookies.set("session", session_token)
    client.cookies.set("csrf_token", csrf_token)
    try:
        dashboard = await client.get("/dashboard")
        assert f'action="/galleries/{gallery.id}/delete"' not in dashboard.text
        response = await client.post(
            f"/galleries/{gallery.id}/delete",
            data={"confirm_delete": "yes", "csrf_token": csrf_token},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    assert response.status_code == 404
    assert fake.deleted == []
    assert await verify_db.get(Gallery, gallery.id) is not None


async def test_frontend_delete_rejects_invalid_csrf(client, db, owner, verify_db):
    gallery = await _gallery_with_files(db, owner)
    fake = _RecordingStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    session_token = create_access_token(str(owner.id))
    client.cookies.set("session", session_token)
    client.cookies.set("csrf_token", _mint_csrf_token(session_token))
    try:
        response = await client.post(
            f"/galleries/{gallery.id}/delete",
            data={"confirm_delete": "yes", "csrf_token": "invalid-submission"},
        )
    finally:
        app.dependency_overrides.pop(get_storage, None)

    assert response.status_code == 403
    assert fake.deleted == []
    assert await verify_db.get(Gallery, gallery.id) is not None
