"""Integration tests for the selection export endpoint (r84: txt + marked)."""

from app.db.models import (
    Gallery,
    GalleryStatus,
    Image,
    SelectionAction,
    SelectionEvent,
    ShareSession,
)


async def _gallery_with_marks(db, owner):
    """A shared gallery with 3 images: img1 selected, img2 favorited, img3 untouched."""
    gallery = Gallery(owner_id=owner.id, name="Export Test", status=GalleryStatus.shared)
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    images = [
        Image(
            gallery_id=gallery.id,
            storage_key=f"proofs/img{i}.jpg",
            filename=f"img{i}.jpg",
            content_type="image/jpeg",
            sort_order=i,
        )
        for i in (1, 2, 3)
    ]
    session = ShareSession(gallery_id=gallery.id)
    db.add_all([*images, session])
    await db.commit()
    for img in images:
        await db.refresh(img)
    await db.refresh(session)

    db.add_all(
        [
            SelectionEvent(image_id=images[0].id, share_session_id=session.id, action=SelectionAction.select),
            SelectionEvent(image_id=images[1].id, share_session_id=session.id, action=SelectionAction.favorite),
        ]
    )
    await db.commit()
    return gallery


async def test_export_txt_marked_is_comma_separated_filenames(client, db, owner, auth_headers):
    """format=txt + filter=marked yields a single comma-separated line of the
    selected OR favorited filenames (with extension), ordered by sort_order —
    the paste-in format for Lightroom Classic / Capture One."""
    gallery = await _gallery_with_marks(db, owner)

    resp = await client.get(
        f"/api/v1/galleries/{gallery.id}/export",
        params={"format": "txt", "filter": "marked"},
        headers=auth_headers(owner),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "img1.jpg, img2.jpg" == resp.text
    assert "img3.jpg" not in resp.text
    assert 'filename="Export_Test_filenames.txt"' in resp.headers["content-disposition"]


async def test_export_marked_filter_excludes_untouched(client, db, owner, auth_headers):
    """filter=marked in JSON returns exactly the touched images (selected OR favorited)."""
    gallery = await _gallery_with_marks(db, owner)

    resp = await client.get(
        f"/api/v1/galleries/{gallery.id}/export",
        params={"format": "json", "filter": "marked"},
        headers=auth_headers(owner),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["exported_count"] == 2
    names = {s["filename"] for s in body["selections"]}
    assert names == {"img1.jpg", "img2.jpg"}


async def test_export_rejects_unknown_format(client, db, owner, auth_headers):
    """format is enum-validated — an unknown value is a 422, never executed."""
    gallery = await _gallery_with_marks(db, owner)

    resp = await client.get(
        f"/api/v1/galleries/{gallery.id}/export",
        params={"format": "xml"},
        headers=auth_headers(owner),
    )

    assert resp.status_code == 422
