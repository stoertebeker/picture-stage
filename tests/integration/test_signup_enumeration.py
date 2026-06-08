"""Integration tests for the signup account-enumeration guard (picture-stage-42q).

A repeated signup with an already-known email (as User OR PendingSignup) must
NOT reveal that the account exists: same neutral response, no 409, no new/over-
written PendingSignup, existing password untouched. Covers the JSON API path.

Run against real PostgreSQL in CI (the sandbox cannot reach the DB).
"""

from sqlalchemy import func, select

from app.auth.passwords import hash_password, verify_password
from app.db.models import PendingSignup, UserStatus
from tests.integration.conftest import make_user

NEUTRAL_MESSAGE = "Signup received. Please verify your email."


async def _pending_count(db, email: str) -> int:
    result = await db.execute(select(func.count()).select_from(PendingSignup).where(PendingSignup.email == email))
    return result.scalar() or 0


async def test_fresh_signup_creates_pending_and_returns_neutral(client, db):
    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "fresh@example.com", "password": "correct horse"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["message"] == NEUTRAL_MESSAGE
    # Token must NOT leak in the response body (would itself enable enumeration).
    assert "verification_token" not in body
    assert await _pending_count(db, "fresh@example.com") == 1


async def test_signup_existing_user_is_neutral_and_idempotent(client, db):
    # An active user already owns this email, with a known password hash.
    existing = await make_user(db, "taken@example.com", status=UserStatus.active)
    original_hash = existing.password_hash

    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "taken@example.com", "password": "attacker-chosen-pw"},
    )

    # Same neutral response as a fresh signup — no 409, no existence hint.
    assert resp.status_code == 201
    assert resp.json()["message"] == NEUTRAL_MESSAGE

    # No PendingSignup created for an existing user.
    assert await _pending_count(db, "taken@example.com") == 0

    # Existing account password is UNCHANGED (no account-takeover vector).
    await db.refresh(existing)
    assert existing.password_hash == original_hash
    assert verify_password("pw-not-used", existing.password_hash)
    assert not verify_password("attacker-chosen-pw", existing.password_hash)


async def test_signup_existing_pending_is_neutral_and_not_overwritten(client, db):
    # A PendingSignup already exists for this email with a specific password hash.
    original_hash = hash_password("first-attempt-pw")
    db.add(
        PendingSignup(
            email="pending@example.com",
            password_hash=original_hash,
            verification_token_hash=b"orig-hash",
            verification_token_salt=b"orig-salt",
        )
    )
    await db.commit()

    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "pending@example.com", "password": "second-attempt-pw"},
    )

    assert resp.status_code == 201
    assert resp.json()["message"] == NEUTRAL_MESSAGE

    # Still exactly one PendingSignup, and it was NOT overwritten.
    assert await _pending_count(db, "pending@example.com") == 1
    result = await db.execute(select(PendingSignup).where(PendingSignup.email == "pending@example.com"))
    pending = result.scalar_one()
    assert pending.password_hash == original_hash
    assert pending.verification_token_hash == b"orig-hash"


async def test_signup_short_password_still_rejected(client, db):
    # The password-length guard stays a real 422 — it leaks nothing about accounts.
    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "shortpw@example.com", "password": "short"},
    )

    assert resp.status_code == 422
    assert await _pending_count(db, "shortpw@example.com") == 0
