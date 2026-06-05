"""Integration tests for guest selection session binding."""

from sqlalchemy import select

from app.auth.passwords import hash_token
from app.db.models import Gallery, GalleryStatus, Image, SelectionEvent, ShareSession


async def test_selection_event_uses_request_session_id(client, db, verify_db, owner):
    token = "guest-selection-session-token"
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

    image = Image(
        gallery_id=gallery.id,
        storage_key="proofs/image-1.jpg",
        filename="image-1.jpg",
        content_type="image/jpeg",
        sort_order=1,
    )
    session_a = ShareSession(gallery_id=gallery.id)
    session_b = ShareSession(gallery_id=gallery.id)
    db.add_all([image, session_a, session_b])
    await db.commit()
    await db.refresh(image)
    await db.refresh(session_a)
    await db.refresh(session_b)

    response = await client.post(
        f"/g/{token}/selections",
        json={
            "image_id": str(image.id),
            "session_id": str(session_a.id),
            "action": "select",
        },
    )

    assert response.status_code == 201
    result = await verify_db.execute(select(SelectionEvent).where(SelectionEvent.image_id == image.id))
    event = result.scalar_one()
    assert event.share_session_id == session_a.id
    assert event.share_session_id != session_b.id


async def test_selection_event_rejects_session_from_other_gallery(client, db, owner):
    token = "guest-selection-wrong-gallery-token"
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Shared proofing",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
    )
    other_gallery = Gallery(owner_id=owner.id, name="Other proofing", status=GalleryStatus.shared)
    db.add_all([gallery, other_gallery])
    await db.commit()
    await db.refresh(gallery)
    await db.refresh(other_gallery)

    image = Image(
        gallery_id=gallery.id,
        storage_key="proofs/image-1.jpg",
        filename="image-1.jpg",
        content_type="image/jpeg",
        sort_order=1,
    )
    wrong_session = ShareSession(gallery_id=other_gallery.id)
    db.add_all([image, wrong_session])
    await db.commit()
    await db.refresh(image)
    await db.refresh(wrong_session)

    response = await client.post(
        f"/g/{token}/selections",
        json={
            "image_id": str(image.id),
            "session_id": str(wrong_session.id),
            "action": "select",
        },
    )

    assert response.status_code == 403
