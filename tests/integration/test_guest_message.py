"""Integration tests for the optional photographer note shown in the guest viewer (dii).

A gallery may carry a free-text ``guest_message`` set by the photographer. The guest
viewer renders it (autoescaped) above the image grid, and omits the block entirely
when no message is set.
"""

from app.auth.passwords import hash_token
from app.db.models import Gallery, GalleryStatus


async def _make_shared_gallery(db, owner_id, token: str, guest_message=None) -> Gallery:
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner_id,
        name="Geteilte Galerie",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        password_hash=None,
        guest_message=guest_message,
    )
    db.add(gallery)
    await db.commit()
    await db.refresh(gallery)
    return gallery


async def test_guest_viewer_renders_message_when_set(client, db, owner):
    token = "msg-token-abc123"
    await _make_shared_gallery(db, owner.id, token, guest_message="Bitte 10 Favoriten auswählen")

    resp = await client.get(f"/g/{token}")
    assert resp.status_code == 200
    assert "Bitte 10 Favoriten auswählen" in resp.text


async def test_guest_viewer_omits_message_block_when_unset(client, db, owner):
    token = "msg-token-def456"
    await _make_shared_gallery(db, owner.id, token, guest_message=None)

    resp = await client.get(f"/g/{token}")
    assert resp.status_code == 200
    # Eyebrow label only appears when a message is present.
    assert "Nachricht vom Fotografen" not in resp.text
    assert "Message from the photographer" not in resp.text


async def test_guest_viewer_escapes_message_html(client, db, owner):
    token = "msg-token-ghi789"
    await _make_shared_gallery(db, owner.id, token, guest_message="<script>alert('xss')</script>")

    resp = await client.get(f"/g/{token}")
    assert resp.status_code == 200
    # Photographer free-text is rendered to guests -> must be autoescaped, never raw.
    assert "<script>alert('xss')</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text
