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


async def test_selection_persists_across_sessions_same_gallery(client, db, owner):
    """Magic-link = one model: a selection made in session A is visible from session B
    (e.g. picking on the phone, then continuing on the PC)."""
    token = "guest-selection-cross-session-token"
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Cross-device proofing",
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

    # Pick the image in session A (the "phone").
    pick = await client.post(
        f"/g/{token}/selections",
        json={"image_id": str(image.id), "session_id": str(session_a.id), "action": "select"},
    )
    assert pick.status_code == 201

    # Read the summary via session B (the "PC") — selection must be visible.
    summary = await client.get(f"/g/{token}/selections", params={"session_id": str(session_b.id)})
    assert summary.status_code == 200
    body = summary.json()
    assert body["selected_count"] == 1
    assert any(s["image_id"] == str(image.id) and s["selected"] for s in body["selections"])


async def test_selection_rejected_after_gallery_completed(client, db, owner):
    """Once the review is completed the selection is read-only gallery-wide."""
    token = "guest-selection-completed-token"
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Completed proofing",
        status=GalleryStatus.completed,
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
    session = ShareSession(gallery_id=gallery.id)
    db.add_all([image, session])
    await db.commit()
    await db.refresh(image)
    await db.refresh(session)

    response = await client.post(
        f"/g/{token}/selections",
        json={"image_id": str(image.id), "session_id": str(session.id), "action": "select"},
    )

    assert response.status_code == 403
