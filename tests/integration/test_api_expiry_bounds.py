"""API expiry boundaries stay finite; existing PostgreSQL infinities never cause 500."""

import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from sqlalchemy import func, select, text

from app.auth.passwords import hash_password, hash_token
from app.db.models import Gallery, GalleryStatus

INVALID_EXPIRIES = [
    "9999-12-31T23:59:59.999999Z",
    "9999-12-31T23:59:59.999999",
    "9999-12-31T22:59:59.999999-01:00",
    "9999-12-31T23:59:59.999999-01:00",
    "0001-01-01T00:00:00+01:00",
]
FINITE_EXPIRIES = [
    "9999-12-31T23:59:59.999998Z",
    "9999-12-31T23:59:59.999998",
    "9999-12-31T23:59:59.999999+02:00",
    "9999-12-31T22:59:59.999998-01:00",
]


def _aware(raw):
    value = datetime.fromisoformat(raw)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("expiry", INVALID_EXPIRIES)
async def test_api_rejects_unrepresentable_expiry_without_mutation(
    client, db, owner, auth_headers, verify_db, operation, expiry
):
    original = datetime(datetime.now(UTC).year + 1, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    existing = Gallery(owner_id=owner.id, name="Preserve original", expires_at=original)
    db.add(existing)
    await db.commit()
    headers = auth_headers(owner)
    if operation == "create":
        response = await client.post(
            "/api/v1/galleries", json={"name": "Must not exist", "expires_at": expiry}, headers=headers
        )
    else:
        response = await client.patch(
            f"/api/v1/galleries/{existing.id}",
            json={"name": "Must not replace original", "expires_at": expiry},
            headers=headers,
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "expires_at"]
    saved = await verify_db.get(Gallery, existing.id)
    assert saved.name == "Preserve original"
    assert saved.expires_at == original
    count = await verify_db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == owner.id))
    assert count == 1


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("expiry", FINITE_EXPIRIES)
async def test_finite_api_boundaries_roundtrip_and_remain_accessible(
    client, owner, auth_headers, verify_db, operation, expiry
):
    headers = auth_headers(owner)
    response = await client.post(
        "/api/v1/galleries",
        json={"name": "Finite boundary", **({"expires_at": expiry} if operation == "create" else {})},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    gallery_id = response.json()["id"]
    if operation == "update":
        response = await client.patch(f"/api/v1/galleries/{gallery_id}", json={"expires_at": expiry}, headers=headers)
        assert response.status_code == 200, response.text
    actual = datetime.fromisoformat(response.json()["expires_at"])
    assert actual.tzinfo is not None
    assert actual == _aware(expiry)
    assert actual.microsecond == _aware(expiry).microsecond
    finite = await verify_db.scalar(
        text("SELECT isfinite(expires_at) FROM galleries WHERE id = :id"), {"id": uuid.UUID(gallery_id)}
    )
    assert finite is True
    shared = await client.post(f"/api/v1/galleries/{gallery_id}/share", json={}, headers=headers)
    assert shared.status_code == 200
    guest_path = urlsplit(shared.json()["share_url"]).path
    guest = await client.get(guest_path)
    assert guest.status_code == 200
    assert "Finite boundary" in guest.text


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize(("expiry", "status"), [("infinity", 200), ("-infinity", 410)])
async def test_existing_infinite_expiry_keeps_guest_and_password_semantics(
    client, db, owner, verify_db, protected, expiry, status
):
    token = uuid.uuid4().hex
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Existing infinity",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        password_hash=hash_password("existing-password") if protected else None,
    )
    db.add(gallery)
    await db.commit()
    await db.execute(
        text("UPDATE galleries SET expires_at = CAST(CAST(:expiry AS text) AS timestamptz) WHERE id = :id"),
        {"expiry": expiry, "id": gallery.id},
    )
    await db.commit()
    saved = await verify_db.get(Gallery, gallery.id)
    assert saved.expires_at == (datetime.max if expiry == "infinity" else datetime.min)
    guest_path = f"/g/{token}"
    viewer = await client.get(guest_path)
    assert viewer.status_code == status
    images = await client.get(f"{guest_path}/images")
    assert images.status_code == (403 if protected and status == 200 else status)
    if protected and status == 200:
        assert 'name="password"' in viewer.text
        unlocked = await client.post(f"{guest_path}/verify-password", data={"password": "existing-password"})
        assert unlocked.status_code == 200
        assert 'data-session-id="' in unlocked.text
    # Reading a legacy value must not rewrite its storage semantics.
    stored = await verify_db.scalar(text("SELECT expires_at::text FROM galleries WHERE id = :id"), {"id": gallery.id})
    assert stored == expiry
