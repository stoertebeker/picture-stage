"""Integration tests for the gallery deletion workflow (fbr.4).

Verifies full deletion + GDPR-style audit-log anonymisation + tenant isolation
against a real database.
"""

import uuid

from sqlalchemy import select

from app.db.models import AuditLog, Gallery, GalleryStatus


async def _make_gallery(db, owner_id, name="Test-Galerie") -> Gallery:
    gallery = Gallery(owner_id=owner_id, name=name, status=GalleryStatus.draft)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)
    return gallery


async def test_delete_removes_gallery_record(client, db, owner, auth_headers):
    gallery = await _make_gallery(db, owner.id)

    resp = await client.delete(f"/api/v1/galleries/{gallery.id}", headers=auth_headers(owner))
    assert resp.status_code == 204

    db.expire_all()
    found = (await db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none()
    assert found is None, "Gallery row should be gone after deletion"


async def test_delete_anonymizes_audit_log(client, db, owner, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    # Pre-existing audit entry with PII
    entry = AuditLog(
        gallery_id=gallery.id,
        event_type="gallery_viewed",
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0 (snoop)",
    )
    db.add(entry)
    await db.commit()

    resp = await client.delete(f"/api/v1/galleries/{gallery.id}", headers=auth_headers(owner))
    assert resp.status_code == 204

    db.expire_all()
    rows = (await db.execute(select(AuditLog))).scalars().all()
    # Audit rows survive deletion (compliance trail), but PII is stripped and
    # the gallery reference is detached.
    assert len(rows) >= 1, "Audit entries must survive gallery deletion"
    for row in rows:
        assert row.ip_address is None, "IP must be anonymised"
        assert row.user_agent is None, "User-Agent must be anonymised"
        assert row.gallery_id is None, "Audit entry must be detached from gallery"
    # The original event type is preserved, and a deletion event was recorded.
    event_types = {row.event_type for row in rows}
    assert "gallery_viewed" in event_types
    assert "gallery_deleted" in event_types


async def test_delete_tenant_isolation(client, db, owner, other_user, auth_headers):
    gallery = await _make_gallery(db, owner.id)

    # Intruder must not be able to delete a gallery they do not own.
    resp = await client.delete(f"/api/v1/galleries/{gallery.id}", headers=auth_headers(other_user))
    assert resp.status_code == 404

    db.expire_all()
    found = (await db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none()
    assert found is not None, "Gallery must still exist after a foreign deletion attempt"


async def test_delete_nonexistent_gallery(client, owner, auth_headers):
    resp = await client.delete(f"/api/v1/galleries/{uuid.uuid4()}", headers=auth_headers(owner))
    assert resp.status_code == 404


async def test_delete_requires_auth(client, db, owner):
    gallery = await _make_gallery(db, owner.id)
    resp = await client.delete(f"/api/v1/galleries/{gallery.id}")
    assert resp.status_code in (401, 403), "Unauthenticated deletion must be rejected"
