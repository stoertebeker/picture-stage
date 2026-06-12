"""Integration test: completing a review emails the gallery owner (picture-stage-16l).

Drives the real POST /g/{token}/complete endpoint against Postgres and asserts the
config-free owner alert is invoked with the owner's own address and the completion
payload. The SMTP send itself is patched (the unit tests cover the transport).
"""

from unittest.mock import AsyncMock, patch

from app.auth.passwords import hash_token
from app.db.models import Gallery, GalleryStatus, Image, ShareSession


async def test_complete_review_notifies_owner(client, db, owner):
    token = "complete-notify-token-abc"
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Notify Shoot",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
    )
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    image = Image(gallery_id=gallery.id, storage_key="p/1.jpg", filename="1.jpg", sort_order=1)
    session = ShareSession(gallery_id=gallery.id)
    db.add_all([image, session])
    await db.commit()
    await db.refresh(session)

    with patch("app.guest.router.notify_owner_gallery_completed", new=AsyncMock()) as notify:
        resp = await client.post(f"/g/{token}/complete", params={"session_id": str(session.id)})

    assert resp.status_code == 200, resp.text
    notify.assert_awaited_once()
    # Recipient is the owner's own DB address; payload carries the gallery name.
    args = notify.await_args.args
    assert args[0] == owner.email
    assert args[1]["gallery_name"] == "Notify Shoot"


async def test_complete_review_owner_lookup_uses_correct_email(client, db, owner):
    """The recipient must be resolved from the gallery's owner_id, not hardcoded."""
    token = "complete-notify-token-def"
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Owner Lookup",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
    )
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)

    session = ShareSession(gallery_id=gallery.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)

    with patch("app.guest.router.notify_owner_gallery_completed", new=AsyncMock()) as notify:
        resp = await client.post(f"/g/{token}/complete", params={"session_id": str(session.id)})

    assert resp.status_code == 200, resp.text
    assert notify.await_args.args[0] == owner.email
