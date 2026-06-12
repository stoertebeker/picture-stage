"""Regression tests for the guest complete-review endpoint contract (picture-stage-4gr).

The frontend JS (completeReview()) POSTs `{ "session_id": ... }` as a JSON body.
The endpoint previously declared session_id as a bare uuid.UUID, which FastAPI
treats as a *query* parameter, so the real browser button got 422 (silently — the
JS never checked the status) and galleries were never completed via the web UI.
These tests pin the body contract so the mismatch cannot return.
"""

from sqlalchemy import select

from app.auth.passwords import hash_token
from app.db.models import Gallery, GalleryStatus, ShareSession


async def _shared_gallery_with_session(db, owner_id, token: str):
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner_id,
        name="Complete contract",
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
    return gallery, session


async def test_complete_accepts_session_id_in_json_body(client, db, verify_db, owner):
    """The exact shape the frontend JS sends must return 200 and complete the gallery."""
    token = "complete-contract-body"
    gallery, session = await _shared_gallery_with_session(db, owner.id, token)

    resp = await client.post(f"/g/{token}/complete", json={"session_id": str(session.id)})

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["session_completed"] is True
    # Gallery really transitioned (read-only gate now applies gallery-wide).
    fresh = await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id))
    assert fresh == GalleryStatus.completed


async def test_complete_rejects_missing_session_id(client, db, owner):
    """An empty body is a 422 — session_id is required in the body, not the query."""
    token = "complete-contract-empty"
    await _shared_gallery_with_session(db, owner.id, token)

    resp = await client.post(f"/g/{token}/complete", json={})

    assert resp.status_code == 422
    # The missing field must be located in the body, proving it is no longer a query param.
    locs = [tuple(err["loc"]) for err in resp.json()["detail"]]
    assert ("body", "session_id") in locs
