"""Untrusted spreadsheet cells stay text in both CSV exports."""

import csv
import io

from app.db.models import AuditLog, Gallery, GalleryStatus, Image, SelectionAction, SelectionEvent, ShareSession


async def test_selection_csv_neutralizes_formula_cells_only(client, db, owner, auth_headers):
    gallery = Gallery(owner_id=owner.id, name="CSV safety", status=GalleryStatus.shared)
    db.add(gallery)
    await db.flush()
    session = ShareSession(gallery_id=gallery.id)
    db.add(session)
    await db.flush()

    dangerous = [
        '=HYPERLINK("https://example.invalid/a,b")',
        '=1+2",=3+4\n@SUM(1,2)',
        " +1+2",
        "\t@SUM(1,2)",
        "\x01=1+1",
        "\r\n-3+4",
        "\u3000\uff1d1+1",
    ]
    safe = ['normal, "quoted"\ncomment', "already 'quoted", "file-name.jpg", ""]
    values = dangerous + safe
    for idx, value in enumerate(values):
        image = Image(
            gallery_id=gallery.id,
            storage_key=f"test/{idx}.jpg",
            filename=f"=file{idx}.jpg" if idx == 0 else f"normal{idx}.jpg",
            content_type="image/jpeg",
            sort_order=idx,
        )
        db.add(image)
        await db.flush()
        db.add(
            SelectionEvent(
                image_id=image.id, share_session_id=session.id, action=SelectionAction.comment, comment=value
            )
        )
    await db.commit()

    url = f"/api/v1/galleries/{gallery.id}/export"
    response = await client.get(url, params={"format": "csv"}, headers=auth_headers(owner))
    assert response.status_code == 200
    assert response.text.startswith('"filename","selected","favorited","comment"')
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == len(values)
    assert [row["comment"] for row in rows] == ["'" + v for v in dangerous] + safe
    assert rows[0]["filename"] == "'=file0.jpg"
    assert rows[1]["filename"] == "normal1.jpg"

    # The data remains verbatim outside the spreadsheet-oriented export.
    json_response = await client.get(url, params={"format": "json"}, headers=auth_headers(owner))
    assert json_response.status_code == 200
    assert [row["comment"] or "" for row in json_response.json()["selections"]] == values
    text_response = await client.get(url, params={"format": "txt"}, headers=auth_headers(owner))
    assert text_response.status_code == 200
    assert text_response.text.startswith("=file0.jpg, normal1.jpg")


async def test_audit_csv_neutralizes_user_agent_without_changing_normal_text(client, db, owner, auth_headers):
    gallery = Gallery(owner_id=owner.id, name="Audit CSV", status=GalleryStatus.draft)
    db.add(gallery)
    await db.flush()
    db.add_all(
        [
            AuditLog(gallery_id=gallery.id, event_type="viewed", user_agent="  =1+1", ip_address="198.51.100.1"),
            AuditLog(
                gallery_id=gallery.id, event_type="viewed", user_agent="browser, normal", ip_address="198.51.100.2"
            ),
            # Model fields can also contain legacy values and attacker-supplied
            # request metadata; each resulting text cell must be guarded.
            AuditLog(
                gallery_id=gallery.id,
                event_type="=1+1",
                ip_address="+12",
                user_agent="\t=3+4",
                details='=1+2",=3+4\n@SUM(1,2)',
            ),
        ]
    )
    await db.commit()

    response = await client.get(f"/api/v1/galleries/{gallery.id}/audit-log/export", headers=auth_headers(owner))
    assert response.status_code == 200
    assert response.text.startswith('"id","event_type",')
    rows = list(csv.DictReader(io.StringIO(response.text)))
    by_address = {row["ip_address"]: row for row in rows}
    assert by_address["198.51.100.1"]["user_agent"] == "'  =1+1"
    assert by_address["198.51.100.2"]["user_agent"] == "browser, normal"
    assert by_address["'+12"]["event_type"] == "'=1+1"
    assert by_address["'+12"]["user_agent"] == "'\t=3+4"
    assert by_address["'+12"]["details"] == "'=1+2\",=3+4\n@SUM(1,2)"
    assert all(None not in row for row in rows)  # No injected extra CSV cells.
