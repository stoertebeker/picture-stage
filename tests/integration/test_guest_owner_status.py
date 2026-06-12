"""Integration tests for picture-stage-cxs.

A disabled (or otherwise non-login-allowed) photographer's share links must stop
resolving for guests. The check lives in both token resolvers — the HTML viewer
(app/frontend/guest.py) and the JSON API (app/guest/router.py) — so each is
exercised through an endpoint that is uniquely reachable on it.
"""

from app.auth.passwords import hash_token
from app.db.models import Gallery, GalleryStatus, Image, ShareSession, UserStatus

from .conftest import make_user


async def _make_shared_gallery(db, owner, token: str) -> Gallery:
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Shared proofing",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
    )
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)
    return gallery


async def test_guest_viewer_blocked_when_owner_disabled(client, db):
    """HTML viewer (frontend resolver): a disabled owner's link 404s."""
    owner = await make_user(db, "blocked@test.local", status=UserStatus.disabled)
    token = "cxs-disabled-owner-token"
    await _make_shared_gallery(db, owner, token)

    response = await client.get(f"/g/{token}", headers={"accept": "text/html"})

    assert response.status_code == 404


async def test_guest_viewer_ok_for_active_owner(client, db):
    """Counter-check: an active owner's link still resolves (the join must only
    filter the owner status, not break token resolution)."""
    owner = await make_user(db, "active@test.local", status=UserStatus.active)
    token = "cxs-active-owner-token"
    await _make_shared_gallery(db, owner, token)

    response = await client.get(f"/g/{token}", headers={"accept": "text/html"})

    assert response.status_code == 200


async def test_guest_access_restored_after_owner_reenabled(client, db):
    """Reversible by design: re-enabling the owner restores guest access without
    touching any share session (the resolver gate, not a session mutation)."""
    owner = await make_user(db, "toggle@test.local", status=UserStatus.disabled)
    token = "cxs-reenable-token"
    await _make_shared_gallery(db, owner, token)

    blocked = await client.get(f"/g/{token}", headers={"accept": "text/html"})
    assert blocked.status_code == 404

    owner.status = UserStatus.active
    db.add(owner)
    await db.commit()

    restored = await client.get(f"/g/{token}", headers={"accept": "text/html"})
    assert restored.status_code == 200


async def test_guest_api_selections_blocked_when_owner_disabled(client, db):
    """JSON API (api resolver via /selections): a disabled owner's link 404s
    before any selection can be recorded."""
    owner = await make_user(db, "blocked-api@test.local", status=UserStatus.disabled)
    token = "cxs-disabled-owner-api-token"
    gallery = await _make_shared_gallery(db, owner, token)

    image = Image(
        gallery_id=gallery.id,
        storage_key="proofs/image-1.jpg",
        filename="image-1.jpg",
        content_type="image/jpeg",
        sort_order=1,
    )
    session = ShareSession(gallery_id=gallery.id)
    db.add_all([image, session])
    await db.commit()
    await db.refresh(image)
    await db.refresh(session)

    response = await client.post(
        f"/g/{token}/selections",
        json={"image_id": str(image.id), "session_id": str(session.id), "action": "select"},
    )

    assert response.status_code == 404
