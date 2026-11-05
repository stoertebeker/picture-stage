"""Guest authorization changes invalidate sessions without deleting history."""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update

from app.auth.passwords import hash_password, hash_token
from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus, Image, ImagePreview, PreviewVariant, SelectionEvent, ShareSession
from app.db.session import get_db
from app.frontend import guest as guest_frontend
from app.galleries import password_hashing
from app.guest import router as guest_router
from app.security.middleware import _mint_csrf_token
from app.security.rate_limit import limiter
from app.selections.service import lock_selection_budget
from tests.integration.conftest import _override_get_db
from tests.integration.guest_helpers import guest_csrf, password_form, response_session_id

_TOKEN = "revocation-token"
_OLD_PASSWORD = "old-password"
_NEW_PASSWORD = "new-password"


@pytest_asyncio.fixture
async def api_client():
    # The real app serves HTML first for duplicate root/password paths. Cover
    # the JSON router's own session producers as well, using the same DB fixture.
    api = FastAPI()
    api.state.limiter = limiter
    api.include_router(guest_router.router)
    api.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    monkeypatch.setattr(guest_router, "send_notification", AsyncMock())
    monkeypatch.setattr(guest_router, "notify_owner_gallery_completed", AsyncMock())


async def _gallery(db, owner, *, protected=True, token=_TOKEN):
    digest, salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Session revocation",
        status=GalleryStatus.shared,
        share_token_hash=digest,
        share_token_salt=salt,
        password_hash=hash_password("old-password") if protected else None,
    )
    db.add(gallery)
    await db.flush()
    image = Image(gallery_id=gallery.id, storage_key="proofs/private.jpg", filename="private-proof.jpg")
    db.add(image)
    await db.flush()
    db.add(
        ImagePreview(
            image_id=image.id, variant=PreviewVariant.preview, storage_key="proofs/private.webp", width=100, height=100
        )
    )
    await db.commit()
    return gallery, image, token


async def _unlock(client, token, password=_OLD_PASSWORD, *, json_api=False):
    if json_api:
        response = await client.post(f"/g/{token}/verify-password", json={"password": password})
    else:
        response = await password_form(client, f"/g/{token}", password)
    assert response.status_code == 200, response.text
    if json_api:
        return response.json()["session_id"]
    return response_session_id(response)


def _owner_headers(client, owner):
    bearer = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(bearer)
    client.cookies.set("session", bearer)
    client.cookies.set("csrf_token", csrf)
    return {"X-CSRF-Token": csrf, "Authorization": f"Bearer {bearer}"}


async def _change(client, owner, gallery, route="password", password=_NEW_PASSWORD):
    headers = _owner_headers(client, owner)
    if route.startswith("api"):
        path = f"/api/v1/galleries/{gallery.id}/share"
        if route == "api_revoke":
            return await client.delete(path, headers=headers)
        return await client.post(path, json={"password": password}, headers=headers)
    action = "password" if route == "password" else "share"
    path = f"/galleries/{gallery.id}/{action}"
    if route == "html_revoke":
        return await client.delete(path, headers=headers)
    return await client.post(path, data={"password": password or ""}, headers=headers)


async def _select(client, token, image, session_id, action="select"):
    return await client.post(
        f"/g/{token}/selections", json={"image_id": str(image.id), "session_id": str(session_id), "action": action}
    )


async def _wait_for_lock(request, db, *, count=1):
    async with asyncio.timeout(8):
        while True:
            assert not request.done(), "request must wait for the authorization transaction"
            await db.execute(text("SELECT pg_stat_clear_snapshot()"))
            pending = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"
                )
            )
            if pending >= count:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("surface", ["images", "gallery-images", "selections", "select", "complete"])
async def test_password_change_revokes_existing_guest_session(client, db, verify_db, owner, surface):
    gallery, image, token = await _gallery(db, owner)
    old_session = await _unlock(client, token)
    assert (await _select(client, token, image, old_session)).status_code == 201
    assert (await _change(client, owner, gallery)).status_code == 200

    if surface == "select":
        stale = await _select(client, token, image, old_session, "favorite")
    elif surface == "complete":
        stale = await client.post(f"/g/{token}/complete", json={"session_id": old_session})
    else:
        stale = await client.get(f"/g/{token}/{surface}", headers={"X-Guest-Session": old_session})
    assert stale.status_code == (404 if surface == "complete" else 403), stale.text
    assert "private-proof.jpg" not in stale.text
    assert "/media/" not in stale.text
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 1
    assert await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id)) == GalleryStatus.shared

    fresh = await _unlock(client, token, "new-password")
    summary = await client.get(f"/g/{token}/selections", headers={"X-Guest-Session": fresh})
    assert summary.status_code == 200
    assert summary.json()["selected_count"] == 1
    assert (await _select(client, token, image, fresh, "favorite")).status_code == 201
    assert (await client.post(f"/g/{token}/complete", json={"session_id": fresh})).status_code == 200
    old_row = await verify_db.get(ShareSession, uuid.UUID(old_session))
    new_row = await verify_db.get(ShareSession, uuid.UUID(fresh))
    assert old_row.authorization_epoch == 0
    assert new_row.authorization_epoch == 1
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 2


@pytest.mark.parametrize("route", ["password", "html_share", "api_share", "html_revoke", "api_revoke"])
async def test_every_authorization_writer_advances_generation(client, db, verify_db, owner, route):
    gallery, image, token = await _gallery(db, owner)
    old_session = await _unlock(client, token)
    assert (await _select(client, token, image, old_session)).status_code == 201
    changed = await _change(client, owner, gallery, route)
    assert changed.status_code in (200, 204)
    await db.refresh(gallery)
    assert gallery.authorization_epoch == 1
    assert await verify_db.get(ShareSession, uuid.UUID(old_session)) is not None
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 1
    if route.endswith("revoke"):
        assert (await client.get(f"/g/{token}/images", headers={"X-Guest-Session": old_session})).status_code == 404
        assert (await _change(client, owner, gallery, "api_share")).status_code == 200
        await db.refresh(gallery)
        assert gallery.authorization_epoch == 2
    current_token = token if route == "password" else gallery.share_token
    assert (await _select(client, current_token, image, old_session)).status_code == 403
    assert (await client.post(f"/g/{current_token}/complete", json={"session_id": old_session})).status_code == 404
    fresh = await _unlock(client, current_token, "new-password")
    assert (await _select(client, current_token, image, fresh, "favorite")).status_code == 201
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 2


async def test_password_removal_keeps_public_reads_but_requires_new_session_for_writes(client, db, owner, api_client):
    gallery, image, token = await _gallery(db, owner)
    old_session = await _unlock(client, token)
    assert (await _change(client, owner, gallery, password=None)).status_code == 200
    for surface in ("images", "gallery-images", "selections"):
        assert (await client.get(f"/g/{token}/{surface}")).status_code == 200
    assert (await _select(client, token, image, old_session)).status_code == 403
    visit = await api_client.get(f"/g/{token}")
    assert visit.status_code == 200
    assert (await _select(client, token, image, visit.json()["session_id"])).status_code == 201


async def test_password_addition_revokes_previous_passwordless_session(client, db, owner):
    gallery, image, token = await _gallery(db, owner, protected=False)
    visit = await client.get(f"/g/{token}")
    old_session = response_session_id(visit)
    assert (await _change(client, owner, gallery)).status_code == 200
    for surface in ("images", "gallery-images", "selections"):
        assert (await client.get(f"/g/{token}/{surface}", headers={"X-Guest-Session": old_session})).status_code == 403
    assert (await _select(client, token, image, old_session)).status_code == 403
    assert (await client.post(f"/g/{token}/complete", json={"session_id": old_session})).status_code == 404
    fresh = await _unlock(client, token, "new-password")
    assert (await _select(client, token, image, fresh)).status_code == 201


async def test_password_change_waits_for_full_viewer_materialization(client, db, verify_db, owner, monkeypatch):
    gallery, _, token = await _gallery(db, owner)
    entered, release = asyncio.Event(), asyncio.Event()
    # The admitted renderer holds its credential fence through bounded image
    # materialization, signing and template rendering, until its final commit.
    original = guest_frontend.load_guest_image_page

    async def pause(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=15)
        return await original(*args, **kwargs)

    monkeypatch.setattr(guest_frontend, "load_guest_image_page", pause)
    gate = await client.get(f"/g/{token}")
    viewing = asyncio.create_task(
        client.post(
            f"/g/{token}/verify-password",
            data={"password": "old-password", "guest_csrf_token": guest_csrf(gate)},
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=8)
    changing = asyncio.create_task(_change(client, owner, gallery))
    try:
        await _wait_for_lock(changing, verify_db)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(viewing, changing, return_exceptions=True), timeout=15)
    assert viewing.result().status_code == changing.result().status_code == 200
    session_id = response_session_id(viewing.result())
    assert "private-proof.jpg" in viewing.result().text
    assert (await client.get(f"/g/{token}/images", headers={"X-Guest-Session": session_id})).status_code == 403


@pytest.mark.parametrize("producer", ["html_public", "api_public", "html_password", "api_password"])
async def test_all_session_producers_stamp_current_generation(client, api_client, db, owner, producer):
    gallery, image, token = await _gallery(db, owner, protected=False)
    await _change(client, owner, gallery, password="new-password" if "password" in producer else None)
    if "password" in producer:
        session_id = await _unlock(
            api_client if producer.startswith("api") else client,
            token,
            "new-password",
            json_api=producer.startswith("api"),
        )
    else:
        response = await (api_client if producer.startswith("api") else client).get(f"/g/{token}")
        assert response.status_code == 200
        session_id = response.json()["session_id"] if producer.startswith("api") else response_session_id(response)
    row = await db.get(ShareSession, uuid.UUID(session_id))
    assert row.authorization_epoch == 1
    assert (await _select(client, token, image, session_id)).status_code == 201


async def test_two_concurrent_auth_updates_do_not_lose_an_increment(client, db, verify_db, owner, auth_headers):
    gallery, _, _ = await _gallery(db, owner)
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())

    async def change():
        return await client.post(
            f"/api/v1/galleries/{gallery.id}/share", json={"password": "new-password"}, headers=auth_headers(owner)
        )

    first, second = asyncio.create_task(change()), asyncio.create_task(change())
    try:
        await _wait_for_lock(first, verify_db, count=2)
    finally:
        await db.commit()
        await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), timeout=15)
    assert first.result().status_code == second.result().status_code == 200
    assert await verify_db.scalar(select(Gallery.authorization_epoch).where(Gallery.id == gallery.id)) == 2


@pytest.mark.parametrize("producer", ["html_password", "api_password", "html_public", "api_public"])
async def test_queued_session_admission_uses_current_credentials(client, api_client, db, verify_db, owner, producer):
    protected = "password" in producer
    gallery, _, token = await _gallery(db, owner, protected=protected)
    gate = await client.get(f"/g/{token}") if producer == "html_password" else None
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
    requester = api_client if producer.startswith("api") else client
    if protected:
        call = requester.post(
            f"/g/{token}/verify-password",
            **(
                {"json": {"password": "old-password"}}
                if producer.startswith("api")
                else {"data": {"password": "old-password", "guest_csrf_token": guest_csrf(gate)}}
            ),
        )
    else:
        call = requester.get(f"/g/{token}")
    request = asyncio.create_task(call)
    try:
        await _wait_for_lock(request, verify_db)
        await db.execute(
            update(Gallery)
            .where(Gallery.id == gallery.id)
            .values(password_hash=hash_password("new-password"), authorization_epoch=Gallery.authorization_epoch + 1)
        )
    finally:
        await db.commit()
        await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=15)
    response = request.result()
    assert response.status_code == (401 if protected else 200)
    assert "private-proof.jpg" not in response.text
    assert "/media/" not in response.text
    if producer == "api_public":
        assert response.json()["requires_password"] is True
        assert response.json()["session_id"] is None
    assert await verify_db.scalar(select(func.count(ShareSession.id))) == 0


@pytest.mark.parametrize("surface", ["images", "gallery-images", "selections"])
@pytest.mark.parametrize("protected", [False, True])
async def test_queued_read_refreshes_password_and_session_generation(client, db, verify_db, owner, surface, protected):
    gallery, _, token = await _gallery(db, owner, protected=protected)
    session_id = await _unlock(client, token) if protected else None
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
    request = asyncio.create_task(
        client.get(f"/g/{token}/{surface}", headers={"X-Guest-Session": session_id} if session_id else {})
    )
    try:
        await _wait_for_lock(request, verify_db)
        await db.execute(
            update(Gallery)
            .where(Gallery.id == gallery.id)
            .values(password_hash=hash_password("new-password"), authorization_epoch=Gallery.authorization_epoch + 1)
        )
    finally:
        await db.commit()
        await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=15)
    assert request.result().status_code == 403
    assert "/media/" not in request.result().text


@pytest.mark.parametrize("mutation", ["select", "complete"])
async def test_waiting_mutation_rechecks_generation(client, db, verify_db, owner, mutation):
    gallery, image, token = await _gallery(db, owner)
    session_id = await _unlock(client, token)
    if mutation == "select":
        await lock_selection_budget(gallery.id, db)
        request = asyncio.create_task(_select(client, token, image, session_id))
    else:
        await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
        request = asyncio.create_task(client.post(f"/g/{token}/complete", json={"session_id": session_id}))
    try:
        await _wait_for_lock(request, verify_db)
        await db.execute(
            update(Gallery)
            .where(Gallery.id == gallery.id)
            .values(password_hash=hash_password("new-password"), authorization_epoch=Gallery.authorization_epoch + 1)
        )
    finally:
        await db.commit()
        await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=15)
    assert request.result().status_code == (403 if mutation == "select" else 404)
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 0
    assert await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id)) == GalleryStatus.shared


async def test_busy_password_hash_preserves_existing_authorization(client, db, owner, monkeypatch):
    gallery, _, token = await _gallery(db, owner)
    session_id = await _unlock(client, token)
    monkeypatch.setattr(password_hashing, "_MAX_CONCURRENT_HASHES", 0)
    assert (await _change(client, owner, gallery)).status_code == 503
    assert (await client.get(f"/g/{token}/images", headers={"X-Guest-Session": session_id})).status_code == 200
    await db.refresh(gallery)
    assert gallery.authorization_epoch == 0
