"""Guest review transitions are atomic across sessions and owner entry points."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password, hash_token
from app.auth.tokens import create_access_token
from app.config import settings
from app.db.models import (
    Gallery,
    GalleryStatus,
    Image,
    NotificationConfig,
    NotificationDelivery,
    SelectionEvent,
    ShareSession,
    User,
    UserStatus,
)
from app.guest import router as guest_router
from app.notifications import service as notifications
from app.security.middleware import _mint_csrf_token


async def _gallery(db, owner, token, *, protected=False):
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Atomic review",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        password_hash=hash_password("guest-password") if protected else None,
    )
    db.add(gallery)
    await db.flush()
    image = Image(gallery_id=gallery.id, storage_key=f"{gallery.id}/image.jpg", filename="image.jpg")
    sessions = [ShareSession(gallery_id=gallery.id), ShareSession(gallery_id=gallery.id)]
    db.add_all([image, *sessions])
    await db.commit()
    return gallery, image, sessions


@pytest.fixture
def deliveries(monkeypatch):
    configured, owner = AsyncMock(), AsyncMock()
    monkeypatch.setattr(guest_router, "send_notification", configured)
    monkeypatch.setattr(guest_router, "notify_owner_gallery_completed", owner)
    return SimpleNamespace(configured=configured, owner=owner)


async def _complete(client, token, session):
    return await client.post(f"/g/{token}/complete", json={"session_id": str(session.id)})


async def _select(client, token, image, session):
    return await client.post(
        f"/g/{token}/selections",
        json={"image_id": str(image.id), "session_id": str(session.id), "action": "select"},
    )


async def _wait_for_lock(request, db):
    # Observe a real PostgreSQL lock wait, rather than guessing an interleaving
    # from a sleep. Each test runs alone against the dedicated test database.
    async with asyncio.timeout(5):
        while True:
            assert not request.done(), "mutation must wait for the competing transaction"
            # PostgreSQL caches activity snapshots within this observer's
            # transaction; refresh it on every poll to see a newly blocked PID.
            await db.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
            if waiting:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("protected", [False, True])
async def test_revisit_cannot_repeat_email_or_webhook(client, db, verify_db, owner, monkeypatch, protected):
    token = "atomic-notification-replay"
    gallery, _, sessions = await _gallery(db, owner, token, protected=protected)
    db.add(
        NotificationConfig(user_id=owner.id, webhook_url="https://example.com/hook", events={"gallery_completed": True})
    )
    await db.commit()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "photos@example.com")
    monkeypatch.setattr(settings, "notify_owner_on_completion", True)
    smtp, webhook = AsyncMock(), AsyncMock()
    monkeypatch.setattr(notifications.aiosmtplib, "send", smtp)
    monkeypatch.setattr(notifications, "post_webhook", webhook)

    assert (await _complete(client, token, sessions[0])).status_code == 200
    if protected:
        revisit = await client.post(f"/g/{token}/verify-password", data={"password": "guest-password"})
    else:
        revisit = await client.get(f"/g/{token}", headers={"Accept": "text/html"})
    assert revisit.status_code == 200
    fresh = await verify_db.scalar(
        select(ShareSession).where(
            ShareSession.gallery_id == gallery.id, ShareSession.id.not_in([s.id for s in sessions])
        )
    )
    assert fresh is not None
    assert (await _complete(client, token, fresh)).status_code == 409
    assert (await _complete(client, token, sessions[0])).status_code == 409
    smtp.assert_awaited_once()
    webhook.assert_awaited_once()
    assert await verify_db.scalar(select(func.count(NotificationDelivery.id))) == 1
    await verify_db.refresh(fresh)
    assert fresh.completed_at is None


@pytest.mark.parametrize("same_session", [False, True])
async def test_racing_completions_have_one_claimant(
    client, db, verify_db, owner, deliveries, monkeypatch, same_session
):
    token = "atomic-racing-completion"
    _, _, sessions = await _gallery(db, owner, token)
    original = guest_router.resolve_gallery_by_token
    both_resolved = asyncio.Event()
    resolved = 0

    async def resolve(*args):
        nonlocal resolved
        gallery = await original(*args)
        resolved += 1
        if resolved == 2:
            both_resolved.set()
        await asyncio.wait_for(both_resolved.wait(), timeout=5)
        return gallery

    monkeypatch.setattr(guest_router, "resolve_gallery_by_token", resolve)
    responses = await asyncio.wait_for(
        asyncio.gather(
            _complete(client, token, sessions[0]),
            _complete(client, token, sessions[0] if same_session else sessions[1]),
        ),
        timeout=10,
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    deliveries.configured.assert_awaited_once()
    deliveries.owner.assert_awaited_once()
    assert (
        await verify_db.scalar(select(func.count(ShareSession.id)).where(ShareSession.completed_at.is_not(None))) == 1
    )


@pytest.mark.parametrize("finisher", ["guest", "owner_api", "owner_frontend"])
async def test_completion_waits_for_accepted_selection(
    client, db, verify_db, owner, auth_headers, deliveries, monkeypatch, finisher
):
    token = "atomic-selection-first"
    gallery, image, sessions = await _gallery(db, owner, token)
    entered, release = asyncio.Event(), asyncio.Event()
    original = guest_router.assert_selection_budget

    async def pause(*args):
        await original(*args)
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=10)

    monkeypatch.setattr(guest_router, "assert_selection_budget", pause)
    selecting = asyncio.create_task(_select(client, token, image, sessions[0]))
    await asyncio.wait_for(entered.wait(), timeout=5)
    if finisher == "guest":
        finishing = asyncio.create_task(_complete(client, token, sessions[1]))
    elif finisher == "owner_api":
        finishing = asyncio.create_task(
            client.patch(
                f"/api/v1/galleries/{gallery.id}/status", json={"status": "completed"}, headers=auth_headers(owner)
            )
        )
    else:
        bearer = create_access_token(str(owner.id))
        csrf = _mint_csrf_token(bearer)
        client.cookies.set("session", bearer)
        client.cookies.set("csrf_token", csrf)
        finishing = asyncio.create_task(
            client.post(f"/galleries/{gallery.id}/status", data={"status": "completed", "csrf_token": csrf})
        )
    try:
        await _wait_for_lock(finishing, verify_db)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(selecting, finishing, return_exceptions=True), timeout=10)
    assert selecting.result().status_code == 201
    assert finishing.result().status_code == 200
    assert await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id)) == GalleryStatus.completed
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 1
    if finisher == "guest":
        assert deliveries.owner.await_args.args[1]["selected_count"] == 1


async def test_selection_waiting_for_completion_is_rejected(client, db, verify_db, owner, deliveries, monkeypatch):
    token = "atomic-completion-first"
    gallery, image, sessions = await _gallery(db, owner, token)
    entered, release = asyncio.Event(), asyncio.Event()
    original = AsyncSession.commit

    async def pause_commit(session):
        if any(
            isinstance(item, Gallery) and item.id == gallery.id and item.status == GalleryStatus.completed
            for item in session.dirty
        ):
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", pause_commit)
    finishing = asyncio.create_task(_complete(client, token, sessions[0]))
    await asyncio.wait_for(entered.wait(), timeout=5)
    selecting = asyncio.create_task(_select(client, token, image, sessions[1]))
    try:
        await _wait_for_lock(selecting, verify_db)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(finishing, selecting, return_exceptions=True), timeout=10)
    assert finishing.result().status_code == 200
    assert selecting.result().status_code == 403
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 0


@pytest.mark.parametrize("change", ["revoked", "rotated", "expired", "owner_disabled", "archived", "session_deleted"])
async def test_completion_rechecks_access_after_lock_wait(client, db, verify_db, owner, deliveries, change):
    token = "atomic-access-after-wait"
    gallery, _, sessions = await _gallery(db, owner, token)
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
    request = asyncio.create_task(_complete(client, token, sessions[0]))
    try:
        await _wait_for_lock(request, verify_db)
        if change == "revoked":
            await db.execute(
                update(Gallery).where(Gallery.id == gallery.id).values(share_token_hash=None, share_token_salt=None)
            )
        elif change == "rotated":
            token_hash, token_salt = hash_token("replacement-token")
            await db.execute(
                update(Gallery)
                .where(Gallery.id == gallery.id)
                .values(share_token_hash=token_hash, share_token_salt=token_salt)
            )
        elif change == "expired":
            await db.execute(
                update(Gallery)
                .where(Gallery.id == gallery.id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        elif change == "owner_disabled":
            await db.execute(update(User).where(User.id == owner.id).values(status=UserStatus.disabled))
        elif change == "archived":
            await db.execute(update(Gallery).where(Gallery.id == gallery.id).values(status=GalleryStatus.archived))
        else:
            await db.execute(delete(ShareSession).where(ShareSession.id == sessions[0].id))
    finally:
        await db.commit()
        await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=10)
    assert request.result().status_code == (410 if change == "expired" else 404)
    deliveries.configured.assert_not_awaited()
    deliveries.owner.assert_not_awaited()
    assert await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id)) == (
        GalleryStatus.archived if change == "archived" else GalleryStatus.shared
    )


async def test_owner_reopen_allows_a_new_review_with_existing_history(
    client, db, verify_db, owner, auth_headers, deliveries
):
    token = "atomic-reopened-review"
    gallery, image, sessions = await _gallery(db, owner, token)
    assert (await _select(client, token, image, sessions[0])).status_code == 201
    assert (await _complete(client, token, sessions[0])).status_code == 200
    assert (await _complete(client, token, sessions[1])).status_code == 409
    reopened = await client.patch(
        f"/api/v1/galleries/{gallery.id}/status", json={"status": "shared"}, headers=auth_headers(owner)
    )
    assert reopened.status_code == 200
    assert (await _select(client, token, image, sessions[1])).status_code == 201
    assert (await _complete(client, token, sessions[0])).status_code == 409
    assert (await _complete(client, token, sessions[1])).status_code == 200
    assert deliveries.configured.await_count == deliveries.owner.await_count == 2
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 2


async def test_other_gallery_is_not_blocked(client, db, verify_db, owner, deliveries):
    gallery, _, sessions = await _gallery(db, owner, "atomic-blocked")
    _, _, other_sessions = await _gallery(db, owner, "atomic-independent")
    await db.execute(select(Gallery.id).where(Gallery.id == gallery.id).with_for_update())
    request = asyncio.create_task(_complete(client, "atomic-blocked", sessions[0]))
    try:
        await _wait_for_lock(request, verify_db)
        other = await asyncio.wait_for(_complete(client, "atomic-independent", other_sessions[0]), timeout=5)
        assert other.status_code == 200
    finally:
        await db.commit()
        await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), timeout=10)
    assert request.result().status_code == 200


async def test_delivery_failure_does_not_allow_another_completion(client, db, owner, deliveries):
    _, _, sessions = await _gallery(db, owner, "atomic-notification-failure")
    deliveries.configured.side_effect = RuntimeError("notification unavailable")
    assert (await _complete(client, "atomic-notification-failure", sessions[0])).status_code == 200
    assert (await _complete(client, "atomic-notification-failure", sessions[1])).status_code == 409
    deliveries.configured.assert_awaited_once()


async def test_foreign_session_cannot_claim_completion(client, db, verify_db, owner, deliveries):
    gallery, _, _ = await _gallery(db, owner, "atomic-session-binding")
    _, _, sessions = await _gallery(db, owner, "atomic-other-session")
    assert (await _complete(client, "atomic-session-binding", sessions[0])).status_code == 404
    assert await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id)) == GalleryStatus.shared
    deliveries.configured.assert_not_awaited()
    deliveries.owner.assert_not_awaited()
