"""Mailbox proof is required by both admin approval entry points."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text

from app.auth.passwords import hash_password, hash_token
from app.auth.tokens import create_access_token, generate_verification_token, verification_lookup_id
from app.db.models import PendingSignup, User, UserStatus
from app.security.middleware import _mint_csrf_token

from .conftest import TestSession, make_user


async def _admin(client, db):
    admin = await make_user(db, "admin@example.com", UserStatus.admin)
    token = create_access_token(str(admin.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    return {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}


async def _pending(db, *, consumed=False):
    token = generate_verification_token()
    digest, salt = hash_token(token)
    row = PendingSignup(
        email="applicant@example.com",
        password_hash=hash_password("applicant-password"),
        verification_token_hash=b"" if consumed else digest,
        verification_token_salt=b"" if consumed else salt,
        verification_lookup_id=None if consumed else verification_lookup_id(token),
    )
    db.add(row)
    await db.commit()
    return row, token


def _approve_path(route, signup_id):
    return f"/api/v1/admin/approve/{signup_id}" if route == "api" else f"/admin/approve/{signup_id}"


@pytest.mark.parametrize("route", ["api", "html"])
@pytest.mark.parametrize("consumed", [False, True])
async def test_unverified_approval_is_rejected(client, db, verify_db, route, consumed):
    headers = await _admin(client, db)
    row, _ = await _pending(db, consumed=consumed)
    response = await client.post(_approve_path(route, row.id), headers=headers)
    assert response.status_code == 409
    if route == "html":
        assert json.loads(response.headers["HX-Trigger"])["showToast"]["kind"] == "danger"
        assert response.headers["HX-Reswap"] == "none"
    assert await verify_db.get(PendingSignup, row.id) is not None
    assert await verify_db.scalar(select(User).where(User.email == row.email)) is None


@pytest.mark.parametrize("verify_route", ["api", "html"])
@pytest.mark.parametrize("approve_route", ["api", "html"])
async def test_verified_signup_can_be_approved_and_log_in(client, db, verify_db, verify_route, approve_route):
    headers = await _admin(client, db)
    row, token = await _pending(db)
    if verify_route == "api":
        response = await client.post(f"/api/v1/auth/verify-email/{token}")
    else:
        response = await client.get(f"/verify-email/{token}")
    assert response.status_code == 200
    await db.refresh(row)
    assert row.email_verified_at is not None
    assert row.verification_token_hash == row.verification_token_salt == b""
    assert row.verification_lookup_id is None
    # Approval must preserve the actual proof time, including subsecond precision.
    proof_time = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    row.email_verified_at = proof_time
    await db.commit()
    response = await client.post(_approve_path(approve_route, row.id), headers=headers)
    assert response.status_code == (201 if approve_route == "api" else 200)
    user = await verify_db.scalar(select(User).where(User.email == row.email))
    assert user.status == UserStatus.active
    assert user.email_verified_at == proof_time
    assert await verify_db.get(PendingSignup, row.id) is None
    login = await client.post("/api/v1/auth/login", json={"email": row.email, "password": "applicant-password"})
    assert login.status_code == 200
    assert (await client.post(f"/api/v1/auth/verify-email/{token}")).status_code == 404


@pytest.mark.parametrize("route", ["api", "html"])
async def test_duplicate_account_is_preserved(client, db, verify_db, route):
    headers = await _admin(client, db)
    row, _ = await _pending(db)
    row.email_verified_at = datetime.now(UTC)
    existing = await make_user(db, row.email, UserStatus.disabled)
    original_hash = existing.password_hash
    response = await client.post(_approve_path(route, row.id), headers=headers)
    assert response.status_code == 409
    user = await verify_db.get(User, existing.id)
    assert user.password_hash == original_hash
    assert user.status == UserStatus.disabled
    assert user.email_verified_at is None
    assert await verify_db.get(PendingSignup, row.id) is None


async def _wait_for_lock(observer, tasks, count=1):
    async with asyncio.timeout(8):
        while True:
            assert not any(task.done() for task in tasks)
            await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = await observer.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"
                )
            )
            if waiting >= count:
                return
            await asyncio.sleep(0.01)


async def test_two_approvals_serialize(client, db, verify_db):
    headers = await _admin(client, db)
    row, _ = await _pending(db)
    row.email_verified_at = datetime.now(UTC)
    await db.commit()
    await db.execute(select(PendingSignup.id).where(PendingSignup.id == row.id).with_for_update())
    tasks = [
        asyncio.create_task(client.post(_approve_path(route, row.id), headers=headers)) for route in ("api", "html")
    ]
    try:
        await _wait_for_lock(verify_db, tasks, 2)
    finally:
        await db.commit()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    assert sorted(r.status_code for r in responses) in ([200, 404], [201, 404])
    assert await verify_db.scalar(select(func.count()).select_from(User).where(User.email == row.email)) == 1


@pytest.mark.parametrize("route", ["api", "html"])
async def test_verification_rechecks_proof_after_waiting(client, db, verify_db, route):
    row, token = await _pending(db)
    await db.execute(select(PendingSignup.id).where(PendingSignup.id == row.id).with_for_update())
    path = f"/api/v1/auth/verify-email/{token}" if route == "api" else f"/verify-email/{token}"
    task = asyncio.create_task(client.post(path) if route == "api" else client.get(path))
    try:
        await _wait_for_lock(verify_db, [task])
        row.verification_token_hash = row.verification_token_salt = b""
        row.verification_lookup_id = None
    finally:
        await db.commit()
        response = await asyncio.wait_for(task, 15)
    assert response.status_code == 404
    await db.refresh(row)
    assert row.email_verified_at is None


@pytest.mark.parametrize("locale", ["de", "en"])
async def test_admin_list_exposes_proof_and_hides_unverified_approval(client, db, locale):
    headers = await _admin(client, db)
    row, _ = await _pending(db, consumed=True)
    response = await client.get("/admin/signups", headers={**headers, "Accept-Language": locale})
    assert response.status_code == 200
    assert f"/admin/approve/{row.id}" not in response.text
    assert f"/admin/reject/{row.id}" in response.text
    listing = await client.get("/api/v1/admin/pending-signups", headers=headers)
    assert listing.json()[0]["email_verified_at"] is None
    row.email_verified_at = datetime.now(UTC)
    await db.commit()
    response = await client.get("/admin/signups", headers=headers)
    assert f"/admin/approve/{row.id}" in response.text


async def test_signup_verify_approve_lifecycle(client, db, verify_db, monkeypatch):
    headers = await _admin(client, db)
    sender = AsyncMock()
    monkeypatch.setattr("app.auth.router.send_verification_email", sender)
    monkeypatch.setattr("app.auth.router.notify_admins_signup", AsyncMock())
    body = {"email": "new@example.com", "password": "applicant-password"}
    assert (await client.post("/api/v1/auth/signup", json=body)).status_code == 201
    row = await db.scalar(select(PendingSignup))
    assert row.email_verified_at is None
    assert (await client.post(_approve_path("api", row.id), headers=headers)).status_code == 409
    assert (await client.post("/api/v1/auth/login", json=body)).status_code == 401
    token = sender.await_args.args[1]
    assert (await client.post(f"/api/v1/auth/verify-email/{token}")).status_code == 200
    assert await verify_db.scalar(select(User).where(User.email == body["email"])) is None
    assert (await client.post(_approve_path("html", row.id), headers=headers)).status_code == 200
    assert (await client.post("/api/v1/auth/login", json=body)).status_code == 200


async def test_two_verifications_consume_once(client, db, verify_db):
    row, token = await _pending(db)
    await db.execute(select(PendingSignup.id).where(PendingSignup.id == row.id).with_for_update())
    tasks = [
        asyncio.create_task(client.post(f"/api/v1/auth/verify-email/{token}")),
        asyncio.create_task(client.get(f"/verify-email/{token}")),
    ]
    try:
        await _wait_for_lock(verify_db, tasks, 2)
    finally:
        await db.commit()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    assert sorted(response.status_code for response in responses) == [200, 404]
    await db.refresh(row)
    proof_time = row.email_verified_at
    assert proof_time is not None
    assert (await client.post(f"/api/v1/auth/verify-email/{token}")).status_code == 404
    await db.refresh(row)
    assert row.email_verified_at == proof_time


@pytest.mark.parametrize("other", ["approve", "verify"])
@pytest.mark.parametrize("reject_route", ["api", "html"])
async def test_rejection_serializes_with_other_lifecycle_actions(client, db, verify_db, other, reject_route):
    headers = await _admin(client, db)
    row, token = await _pending(db)
    if other == "approve":
        row.email_verified_at = datetime.now(UTC)
        await db.commit()
    await db.execute(select(PendingSignup.id).where(PendingSignup.id == row.id).with_for_update())
    reject_path = f"/api/v1/admin/reject/{row.id}" if reject_route == "api" else f"/admin/reject/{row.id}"
    rejection = asyncio.create_task(
        client.delete(reject_path, headers=headers)
        if reject_route == "api"
        else client.post(reject_path, headers=headers)
    )
    tasks = [rejection]
    try:
        await _wait_for_lock(verify_db, tasks)
        path = _approve_path("api", row.id) if other == "approve" else f"/api/v1/auth/verify-email/{token}"
        tasks.append(asyncio.create_task(client.post(path, headers=headers)))
        await _wait_for_lock(verify_db, tasks, 2)
    finally:
        await db.commit()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    assert responses[0].status_code == (204 if reject_route == "api" else 200)
    assert responses[1].status_code == 404
    assert await verify_db.get(PendingSignup, row.id) is None
    assert await verify_db.scalar(select(User).where(User.email == row.email)) is None


async def test_concurrent_account_insert_preserves_existing_user(client, db, verify_db):
    headers = await _admin(client, db)
    row, _ = await _pending(db)
    row.email_verified_at = datetime.now(UTC)
    await db.commit()
    async with TestSession() as competitor:
        existing = User(email=row.email, password_hash=hash_password("other-password"), status=UserStatus.disabled)
        competitor.add(existing)
        await competitor.flush()
        task = asyncio.create_task(client.post(_approve_path("api", row.id), headers=headers))
        try:
            await _wait_for_lock(verify_db, [task])
        finally:
            await competitor.commit()
            response = await asyncio.wait_for(task, 15)
    assert response.status_code == 409
    user = await verify_db.get(User, existing.id)
    assert user.password_hash == existing.password_hash
    assert user.status == UserStatus.disabled
    assert user.email_verified_at is None
    assert await verify_db.get(PendingSignup, row.id) is None
