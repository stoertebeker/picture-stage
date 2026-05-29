"""Integration tests for the per-gallery audit log viewer + CSV export (fbr.2)."""

from app.db.models import AuditLog, Gallery, GalleryStatus


async def _make_gallery(db, owner_id, name="Audit-Galerie") -> Gallery:
    gallery = Gallery(owner_id=owner_id, name=name, status=GalleryStatus.draft)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)
    return gallery


async def _add_audit(db, gallery_id, event_type, **kw) -> None:
    db.add(AuditLog(gallery_id=gallery_id, event_type=event_type, **kw))
    await db.commit()


async def test_audit_log_returns_entries(client, db, owner, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    await _add_audit(db, gallery.id, "gallery_viewed", ip_address="198.51.100.1")
    await _add_audit(db, gallery.id, "selection_made")

    resp = await client.get(f"/api/v1/galleries/{gallery.id}/audit-log", headers=auth_headers(owner))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["entries"]) == 2
    assert {e["event_type"] for e in body["entries"]} == {"gallery_viewed", "selection_made"}


async def test_audit_log_event_type_filter(client, db, owner, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    await _add_audit(db, gallery.id, "gallery_viewed")
    await _add_audit(db, gallery.id, "gallery_viewed")
    await _add_audit(db, gallery.id, "selection_made")

    resp = await client.get(
        f"/api/v1/galleries/{gallery.id}/audit-log",
        params={"event_type": "gallery_viewed"},
        headers=auth_headers(owner),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(e["event_type"] == "gallery_viewed" for e in body["entries"])


async def test_audit_log_pagination(client, db, owner, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    for _ in range(3):
        await _add_audit(db, gallery.id, "gallery_viewed")

    resp = await client.get(
        f"/api/v1/galleries/{gallery.id}/audit-log",
        params={"page": 1, "per_page": 2},
        headers=auth_headers(owner),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["per_page"] == 2
    assert body["total_pages"] == 2
    assert len(body["entries"]) == 2


async def test_audit_log_tenant_isolation(client, db, owner, other_user, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    await _add_audit(db, gallery.id, "gallery_viewed")

    resp = await client.get(f"/api/v1/galleries/{gallery.id}/audit-log", headers=auth_headers(other_user))
    assert resp.status_code == 404, "A non-owner must not read another gallery's audit log"


async def test_audit_log_export_csv(client, db, owner, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    await _add_audit(db, gallery.id, "gallery_viewed", ip_address="198.51.100.9")

    resp = await client.get(f"/api/v1/galleries/{gallery.id}/audit-log/export", headers=auth_headers(owner))
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    text = resp.text
    assert "event_type" in text  # header row
    assert "gallery_viewed" in text  # data row


async def test_audit_log_export_filename_has_no_crlf(client, db, owner, auth_headers):
    # A malicious gallery name must not leak CR/LF into the Content-Disposition header.
    gallery = await _make_gallery(db, owner.id, name="Shooting\r\nSet-Cookie: evil=1")

    resp = await client.get(f"/api/v1/galleries/{gallery.id}/audit-log/export", headers=auth_headers(owner))
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd, "Content-Disposition must not contain CR/LF"
    assert "Set-Cookie" not in cd or ":" not in cd.split("filename=")[-1]


async def test_audit_log_export_tenant_isolation(client, db, owner, other_user, auth_headers):
    gallery = await _make_gallery(db, owner.id)
    await _add_audit(db, gallery.id, "gallery_viewed")

    resp = await client.get(f"/api/v1/galleries/{gallery.id}/audit-log/export", headers=auth_headers(other_user))
    assert resp.status_code == 404
