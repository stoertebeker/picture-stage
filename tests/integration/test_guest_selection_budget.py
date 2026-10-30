"""Selection log limits hold across sessions and concurrent requests."""

import asyncio

from sqlalchemy import func, select, update

from app.auth.passwords import hash_token
from app.config import settings
from app.db.models import Gallery, GalleryStatus, Image, SelectionAction, SelectionEvent, ShareSession
from app.selections.service import lock_selection_budget


async def make_gallery(db, owner, token):
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Selection budget",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
    )
    db.add(gallery)
    await db.flush()
    images = [Image(gallery_id=gallery.id, storage_key=f"image-{i}", filename=f"image-{i}.jpg") for i in range(2)]
    sessions = [ShareSession(gallery_id=gallery.id), ShareSession(gallery_id=gallery.id)]
    db.add_all([*images, *sessions])
    await db.commit()
    return gallery, images, sessions


def body(image, session, action, comment=None):
    payload = {"image_id": str(image.id), "session_id": str(session.id), "action": action}
    if comment is not None:
        payload["comment"] = comment
    return payload


async def test_comment_limit_and_action_validation(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "max_selection_comment_chars", 4)
    _, images, sessions = await make_gallery(db, owner, "selection-comment-limit")
    path = "/g/selection-comment-limit/selections"
    assert (await client.post(path, json=body(images[0], sessions[0], "comment", "ü🙂ab"))).status_code == 201
    assert (await client.post(path, json=body(images[0], sessions[0], "comment", "ü🙂abc"))).status_code == 422
    assert (await client.post(path, json=body(images[0], sessions[0], "select", "abc"))).status_code == 422
    assert (await client.post(path, json=body(images[0], sessions[0], "select"))).status_code == 201
    summary = await client.get(path, params={"session_id": str(sessions[1].id)})
    assert summary.status_code == 200
    assert summary.json()["selections"][0]["comment"] == "ü🙂ab"
    assert summary.json()["selected_count"] == 1
    assert await db.scalar(select(func.count(SelectionEvent.id))) == 2


async def test_event_budget_is_atomic_across_images_and_sessions(client, db, verify_db, owner, monkeypatch):
    monkeypatch.setattr(settings, "max_selection_events_per_gallery", 1)
    _, images, sessions = await make_gallery(db, owner, "selection-atomic-limit")
    path = "/g/selection-atomic-limit/selections"
    responses = await asyncio.gather(
        client.post(path, json=body(images[0], sessions[0], "select")),
        client.post(path, json=body(images[1], sessions[1], "favorite")),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 1
    assert (await client.post(path, json=body(images[0], sessions[1], "deselect"))).status_code == 409

    # A different gallery retains its own independent budget.
    _, other_images, other_sessions = await make_gallery(db, owner, "selection-other-gallery")
    other = await client.post(
        "/g/selection-other-gallery/selections", json=body(other_images[0], other_sessions[0], "select")
    )
    assert other.status_code == 201


async def test_existing_history_above_lowered_limit_is_preserved_and_readable(client, db, owner, monkeypatch):
    _, images, sessions = await make_gallery(db, owner, "selection-existing-history")
    db.add_all(
        [
            SelectionEvent(image_id=images[0].id, share_session_id=sessions[0].id, action=SelectionAction.select),
            SelectionEvent(
                image_id=images[0].id,
                share_session_id=sessions[0].id,
                action=SelectionAction.comment,
                comment="old long comment",
            ),
        ]
    )
    await db.commit()
    monkeypatch.setattr(settings, "max_selection_events_per_gallery", 1)
    monkeypatch.setattr(settings, "max_selection_comment_chars", 4)
    path = "/g/selection-existing-history/selections"
    result = await client.get(path, params={"session_id": str(sessions[1].id)})
    assert result.status_code == 200
    assert result.json()["selections"][0]["comment"] == "old long comment"
    assert result.json()["selected_count"] == 1
    response = await client.post(path, json=body(images[0], sessions[0], "comment", "old long comment"))
    assert response.status_code == 422
    assert (await client.post(path, json=body(images[0], sessions[0], "deselect"))).status_code == 409
    assert await db.scalar(select(func.count(SelectionEvent.id))) == 2


async def test_revoked_share_is_rechecked_after_waiting_for_budget(client, db, verify_db, owner):
    gallery, images, sessions = await make_gallery(db, owner, "selection-revoked-while-waiting")
    await lock_selection_budget(gallery.id, db)
    request = asyncio.create_task(
        client.post("/g/selection-revoked-while-waiting/selections", json=body(images[0], sessions[0], "select"))
    )
    try:
        await asyncio.sleep(0.2)
        assert not request.done()
        await verify_db.execute(
            update(Gallery).where(Gallery.id == gallery.id).values(share_token_hash=None, share_token_salt=None)
        )
        await verify_db.commit()
    finally:
        await db.commit()
    response = await asyncio.wait_for(request, timeout=5)
    assert response.status_code == 404
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 0
