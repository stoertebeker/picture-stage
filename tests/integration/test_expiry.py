"""Integration tests for optional gallery expiry enforcement on the guest side (fbr.1).

The guest router resolves a share token and rejects access with HTTP 410 once
the gallery's expires_at has passed.
"""

from datetime import UTC, datetime, timedelta

from app.auth.passwords import hash_token
from app.db.models import Gallery, GalleryStatus


async def _make_shared_gallery(db, owner_id, token: str, expires_at=None) -> Gallery:
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner_id,
        name="Geteilte Galerie",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        password_hash=None,
        expires_at=expires_at,
    )
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)
    return gallery


async def test_expired_gallery_returns_410(client, db, owner):
    token = "expired-token-abc123"
    await _make_shared_gallery(db, owner.id, token, expires_at=datetime.now(UTC) - timedelta(days=1))

    resp = await client.get(f"/g/{token}")
    assert resp.status_code == 410, "Access to an expired gallery must be refused with 410 Gone"


async def test_future_expiry_is_accessible(client, db, owner):
    token = "future-token-def456"
    await _make_shared_gallery(db, owner.id, token, expires_at=datetime.now(UTC) + timedelta(days=7))

    resp = await client.get(f"/g/{token}")
    assert resp.status_code == 200, "A gallery whose expiry is in the future must remain accessible"


async def test_no_expiry_is_accessible(client, db, owner):
    token = "noexpiry-token-ghi789"
    await _make_shared_gallery(db, owner.id, token, expires_at=None)

    resp = await client.get(f"/g/{token}")
    assert resp.status_code == 200, "A gallery without an expiry date must remain accessible"


async def test_unknown_token_returns_404(client):
    resp = await client.get("/g/this-token-does-not-exist")
    assert resp.status_code == 404
