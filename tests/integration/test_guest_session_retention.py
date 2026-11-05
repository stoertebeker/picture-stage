"""A capability can consume only a bounded number of durable guest visits."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.auth.token_lookup import token_locator
from app.config import settings
from app.db.models import (
    AuditLog,
    Gallery,
    GalleryStatus,
    SelectionAction,
    SelectionEvent,
    ShareSession,
    ShareSessionCleanupState,
)
from app.db.session import get_db
from app.guest import service
from app.guest.router import router
from app.security.rate_limit import limiter
from tests.integration.conftest import _override_get_db
from tests.integration.guest_helpers import guest_csrf, password_form, response_session_id
from tests.integration.test_guest_browser_credentials import no_notifications  # noqa: F401
from tests.integration.test_guest_session_revocation import (
    _gallery,
    _wait_for_lock,
)


@pytest.fixture(autouse=True)
def session_limits(monkeypatch):
    monkeypatch.setattr(settings, "max_share_sessions_per_gallery", 2)
    monkeypatch.setattr(settings, "share_session_ttl_seconds", 300)
    # A route/IP limiter is intentionally irrelevant to the durable-state cap.
    monkeypatch.setattr(limiter, "enabled", False)


@pytest_asyncio.fixture
async def api_client():
    api = FastAPI()
    api.state.limiter = limiter
    api.include_router(router)
    api.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        yield client


async def gallery_fixture(db, owner, *, protected=False, token="retention-gallery"):  # noqa: S107
    gallery, image, token = await _gallery(db, owner, protected=protected, token=token)
    gallery.share_token_lookup = token_locator(token)
    await db.commit()
    return gallery, image, token


@pytest.mark.parametrize("producer", ["html_public", "api_public", "html_password", "api_password"])
async def test_every_producer_caps_cookie_dropping_and_independent_ips(client, api_client, db, owner, producer):
    protected = producer.endswith("password")
    gallery, _, token = await gallery_fixture(db, owner, protected=protected)
    base = api_client if producer.startswith("api") else client
    responses = []
    for index in range(4):
        # Independent cookie jars and peer IPs cannot buy separate quotas.
        transport = ASGITransport(app=base._transport.app, client=(f"192.0.2.{index + 1}", 1234))
        async with AsyncClient(transport=transport, base_url="http://test") as independent:
            if producer == "api_password":
                response = await independent.post(f"/g/{token}/verify-password", json={"password": "old-password"})
            elif protected:
                response = await password_form(independent, f"/g/{token}", "old-password")
            else:
                response = await independent.get(f"/g/{token}")
            responses.append(response)
    assert [response.status_code for response in responses] == [200, 200, 409, 409]
    assert await db.scalar(select(func.count()).select_from(ShareSession)) == 2
    rows = (await db.scalars(select(ShareSession))).all()
    assert all(290 <= (row.expires_at - datetime.now(UTC)).total_seconds() <= 300 for row in rows)
    other, _, other_token = await gallery_fixture(db, owner, protected=protected, token="other-retention-gallery")
    accepted = (
        await api_client.post(f"/g/{other_token}/verify-password", json={"password": "old-password"})
        if protected
        else await api_client.get(f"/g/{other_token}")
    )
    assert accepted.status_code == 200
    assert (
        await db.scalar(select(func.count()).select_from(ShareSession).where(ShareSession.gallery_id == other.id)) == 1
    )
    assert gallery.id != other.id


@pytest.mark.parametrize("completed", [False, True])
async def test_parallel_admission_is_atomic_including_completed_galleries(api_client, db, owner, completed):
    gallery, _, token = await gallery_fixture(db, owner)
    if completed:
        gallery.status = GalleryStatus.completed
        await db.commit()
    responses = await asyncio.wait_for(asyncio.gather(*(api_client.get(f"/g/{token}") for _ in range(8))), timeout=20)
    assert sorted(response.status_code for response in responses) == [200, 200] + [409] * 6
    assert await db.scalar(select(func.count()).select_from(ShareSession)) == 2


@pytest.mark.parametrize("protected", [False, True])
async def test_reuse_and_existing_credentials_work_above_lowered_budget(
    client, api_client, db, owner, monkeypatch, protected
):
    gallery, image, token = await gallery_fixture(db, owner, protected=protected)
    path = f"/g/{token}"
    visit = await password_form(client, path, "old-password") if protected else await client.get(path)
    session_id = response_session_id(visit)
    db.add_all([ShareSession(gallery_id=gallery.id, authorization_epoch=0) for _ in range(4)])
    await db.commit()
    monkeypatch.setattr(settings, "max_share_sessions_per_gallery", 1)
    reused = await client.get(path)
    assert reused.status_code == 200 and response_session_id(reused) == session_id
    assert "data-lookup-password" not in reused.text
    assert guest_csrf(reused) == guest_csrf(visit)
    for surface in ("images", "selections", "gallery-images"):
        assert (await client.get(f"{path}/{surface}")).status_code == 200
    selected = await client.post(
        f"{path}/selections",
        json={"image_id": str(image.id), "action": "select"},
        headers={"X-Guest-CSRF-Token": guest_csrf(visit)},
    )
    assert selected.status_code == 201, selected.text
    denied = (
        await api_client.post(f"{path}/verify-password", json={"password": "old-password"})
        if protected
        else await api_client.get(path)
    )
    assert denied.status_code == 409
    assert await db.scalar(select(func.count()).select_from(ShareSession)) == 5


async def test_expired_credentials_reject_reads_mutations_completion_but_legacy_null_survives(api_client, db, owner):
    gallery, image, token = await gallery_fixture(db, owner, protected=True)
    visit = await api_client.post(f"/g/{token}/verify-password", json={"password": "old-password"})
    session_id = uuid.UUID(visit.json()["session_id"])
    await db.execute(
        update(ShareSession)
        .where(ShareSession.id == session_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    legacy = ShareSession(gallery_id=gallery.id, authorization_epoch=0)
    db.add(legacy)
    await db.commit()
    for surface in ("images", "selections"):
        assert (
            await api_client.get(f"/g/{token}/{surface}", headers={"X-Guest-Session": str(session_id)})
        ).status_code == 403
        assert (
            await api_client.get(f"/g/{token}/{surface}", headers={"X-Guest-Session": str(legacy.id)})
        ).status_code == 200
    denied = await api_client.post(
        f"/g/{token}/selections", json={"session_id": str(session_id), "image_id": str(image.id), "action": "select"}
    )
    assert denied.status_code == 403
    assert (await api_client.post(f"/g/{token}/complete", json={"session_id": str(session_id)})).status_code == 404
    assert await db.scalar(select(func.count()).select_from(SelectionEvent)) == 0


async def test_cleanup_is_bounded_and_preserves_all_history(api_client, db, owner, monkeypatch):
    gallery, image, token = await gallery_fixture(db, owner)
    monkeypatch.setattr(settings, "max_share_sessions_per_gallery", 20)
    monkeypatch.setattr(service, "SHARE_SESSION_CLEANUP_BATCH_SIZE", 3)
    past = datetime.now(UTC) - timedelta(days=2)
    expired = [ShareSession(gallery_id=gallery.id, expires_at=past + timedelta(seconds=i)) for i in range(6)]
    legacy = ShareSession(gallery_id=gallery.id, authorization_epoch=2**40)
    completed = ShareSession(gallery_id=gallery.id, expires_at=past, completed_at=past)
    db.add_all([*expired, legacy, completed])
    await db.flush()
    event = SelectionEvent(
        image_id=image.id, share_session_id=expired[0].id, action=SelectionAction.comment, comment="keep"
    )
    # Detached audit rows count too: do not filter references by gallery_id.
    audit = AuditLog(actor_session_id=expired[1].id, event_type="legacy_guest", details={"keep": True})
    db.add_all([event, audit])
    await db.commit()
    response = await api_client.get(f"/g/{token}")
    assert response.status_code == 200
    retained = set((await db.scalars(select(ShareSession.id))).all())
    assert expired[2].id not in retained
    assert {row.id for row in [expired[0], expired[1], *expired[3:], legacy, completed]} <= retained
    await db.refresh(event)
    await db.refresh(audit)
    await db.refresh(legacy)
    assert event.comment == "keep" and audit.actor_session_id == expired[1].id
    assert legacy.expires_at is None and legacy.authorization_epoch == 2**40
    assert len(retained) == 8  # eight originals minus one cleanup plus one visit


@pytest.mark.parametrize("reference", ["event", "audit"])
async def test_cleanup_rotates_past_history_and_commits_cursor_on_refusal(
    api_client, db, verify_db, owner, monkeypatch, reference
):
    gallery, image, token = await gallery_fixture(db, owner)
    monkeypatch.setattr(settings, "max_share_sessions_per_gallery", 3)
    monkeypatch.setattr(service, "SHARE_SESSION_CLEANUP_BATCH_SIZE", 1)
    past = datetime.now(UTC) - timedelta(days=2)
    # Equal deadlines exercise the UUID tie-breaker in the durable index key.
    rows = [ShareSession(id=uuid.UUID(int=i + 1), gallery_id=gallery.id, expires_at=past) for i in range(3)]
    db.add_all(rows)
    await db.flush()
    if reference == "event":
        db.add(SelectionEvent(image_id=image.id, share_session_id=rows[0].id, action=SelectionAction.select))
    else:
        db.add(AuditLog(actor_session_id=rows[0].id, event_type="keep-detached"))
    await db.commit()
    first = await api_client.get(f"/g/{token}")
    assert first.status_code == 409
    checkpoint = await verify_db.get(ShareSessionCleanupState, gallery.id)
    assert checkpoint.last_session_id == rows[0].id
    assert await verify_db.scalar(select(func.count()).select_from(ShareSession)) == 3
    for row in rows[1:]:
        accepted = await api_client.get(f"/g/{token}")
        assert accepted.status_code == 200, accepted.text
        assert await verify_db.scalar(select(ShareSession.id).where(ShareSession.id == row.id)) is None
    assert await verify_db.scalar(select(ShareSession.id).where(ShareSession.id == rows[0].id)) == rows[0].id
    assert (
        await verify_db.scalar(select(func.count()).select_from(SelectionEvent if reference == "event" else AuditLog))
        == 1
    )
    assert await verify_db.scalar(select(func.count()).select_from(ShareSessionCleanupState)) == 1


async def test_lowered_budget_keeps_bounded_cleanup_progress_across_rejections(
    api_client, db, verify_db, owner, monkeypatch
):
    gallery, _, token = await gallery_fixture(db, owner)
    monkeypatch.setattr(service, "SHARE_SESSION_CLEANUP_BATCH_SIZE", 1)
    past = datetime.now(UTC) - timedelta(days=1)
    db.add_all([ShareSession(gallery_id=gallery.id, expires_at=past + timedelta(seconds=i)) for i in range(4)])
    await db.commit()
    responses, counts = [], []
    for _ in range(4):
        responses.append((await api_client.get(f"/g/{token}")).status_code)
        counts.append(await verify_db.scalar(select(func.count()).select_from(ShareSession)))
    assert responses == [409, 409, 200, 200]
    assert counts == [3, 2, 2, 2]
    assert await verify_db.scalar(select(func.count()).select_from(ShareSessionCleanupState)) == 1


async def test_cleanup_wraps_to_backdated_tail_and_keeps_live_sessions(api_client, db, verify_db, owner, monkeypatch):
    gallery, _, token = await gallery_fixture(db, owner)
    monkeypatch.setattr(settings, "max_share_sessions_per_gallery", 4)
    monkeypatch.setattr(service, "SHARE_SESSION_CLEANUP_BATCH_SIZE", 1)
    past = datetime.now(UTC) - timedelta(days=1)
    expired = ShareSession(gallery_id=gallery.id, expires_at=past)
    db.add(expired)
    await db.commit()
    first = await api_client.get(f"/g/{token}")
    assert first.status_code == 200
    cursor = await verify_db.get(ShareSessionCleanupState, gallery.id)
    assert cursor.last_session_id == expired.id
    # No expired tail exists after this checkpoint. A newly eligible earlier
    # key must still be reached by the bounded wrap, rather than being stranded.
    earlier = ShareSession(gallery_id=gallery.id, expires_at=past - timedelta(days=1))
    db.add(earlier)
    await db.commit()
    second = await api_client.get(f"/g/{token}")
    assert second.status_code == 200
    assert await verify_db.scalar(select(ShareSession.id).where(ShareSession.id == earlier.id)) is None
    assert await verify_db.scalar(select(func.count()).select_from(ShareSession)) == 2
    await verify_db.refresh(cursor)
    assert cursor.last_session_id == earlier.id
    assert (
        await verify_db.scalar(select(ShareSession.id).where(ShareSession.id == uuid.UUID(first.json()["session_id"])))
        is not None
    )


async def test_cleanup_without_expired_candidates_has_one_empty_checkpoint(api_client, db, verify_db, owner):
    gallery, _, token = await gallery_fixture(db, owner)
    responses = [await api_client.get(f"/g/{token}") for _ in range(3)]
    assert [response.status_code for response in responses] == [200, 200, 409]
    cursor = await verify_db.get(ShareSessionCleanupState, gallery.id)
    assert cursor.last_session_id is None and cursor.last_expires_at is None
    assert await verify_db.scalar(select(func.count()).select_from(ShareSessionCleanupState)) == 1
    assert await verify_db.scalar(select(func.count()).select_from(ShareSession)) == 2


async def test_parallel_admission_commits_cleanup_progress_without_oversubscription(
    api_client, db, verify_db, owner, monkeypatch
):
    gallery, _, token = await gallery_fixture(db, owner)
    monkeypatch.setattr(service, "SHARE_SESSION_CLEANUP_BATCH_SIZE", 1)
    past = datetime.now(UTC) - timedelta(days=1)
    db.add_all([ShareSession(gallery_id=gallery.id, expires_at=past + timedelta(seconds=i)) for i in range(4)])
    await db.commit()
    responses = await asyncio.wait_for(asyncio.gather(*(api_client.get(f"/g/{token}") for _ in range(8))), timeout=20)
    assert sorted(response.status_code for response in responses) == [200, 200] + [409] * 6
    assert await verify_db.scalar(select(func.count()).select_from(ShareSession)) == 2
    assert (
        await verify_db.scalar(select(func.count()).select_from(ShareSession).where(ShareSession.expires_at <= past))
        == 0
    )
    assert await verify_db.scalar(select(func.count()).select_from(ShareSessionCleanupState)) == 1


async def test_cleanup_waits_for_mutating_history_writer(api_client, db, verify_db, owner):
    gallery, image, token = await gallery_fixture(db, owner)
    session = ShareSession(gallery_id=gallery.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    db.add(session)
    await db.commit()
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
    admission = asyncio.create_task(api_client.get(f"/g/{token}"))
    try:
        await _wait_for_lock(admission, verify_db)
        db.add(SelectionEvent(image_id=image.id, share_session_id=session.id, action=SelectionAction.select))
        await db.flush()
    finally:
        await db.commit()
        await asyncio.wait_for(admission, timeout=10)
    assert admission.result().status_code == 200
    assert await verify_db.get(ShareSession, session.id) is not None
    assert await verify_db.scalar(select(func.count()).select_from(SelectionEvent)) == 1


async def test_cleanup_skips_inflight_session_fk_writer_without_erasing_history(api_client, db, verify_db, owner):
    gallery, image, token = await gallery_fixture(db, owner)
    session = ShareSession(gallery_id=gallery.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    db.add(session)
    await db.commit()
    # Even a writer already holding only the session FK's KEY SHARE is safe:
    # retention's FOR UPDATE SKIP LOCKED cannot delete/cascade its new event.
    event = SelectionEvent(image_id=image.id, share_session_id=session.id, action=SelectionAction.select)
    db.add(event)
    await db.flush()
    try:
        response = await asyncio.wait_for(api_client.get(f"/g/{token}"), timeout=10)
        assert response.status_code == 200
    finally:
        await db.commit()
    assert await verify_db.get(ShareSession, session.id) is not None
    assert await verify_db.get(SelectionEvent, event.id) is not None


@pytest.mark.parametrize("mutation", ["selections", "complete"])
async def test_waiting_mutation_rechecks_session_expiry(api_client, db, verify_db, owner, mutation):
    gallery, image, token = await gallery_fixture(db, owner)
    visit = await api_client.get(f"/g/{token}")
    session_id = uuid.UUID(visit.json()["session_id"])
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
    body = {"session_id": str(session_id)}
    if mutation == "selections":
        body.update(image_id=str(image.id), action="select")
    request = asyncio.create_task(api_client.post(f"/g/{token}/{mutation}", json=body))
    try:
        await _wait_for_lock(request, verify_db)
        await db.execute(
            update(ShareSession)
            .where(ShareSession.id == session_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    finally:
        await db.commit()
        await asyncio.wait_for(request, timeout=10)
    assert request.result().status_code == (403 if mutation == "selections" else 404)
    assert await verify_db.scalar(select(func.count()).select_from(SelectionEvent)) == 0
