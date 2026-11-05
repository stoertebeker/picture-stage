"""PostgreSQL-backed CAPTCHA admission and shared one-time challenge ledger."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from altcha import Challenge, Payload, solve_challenge
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.models import CaptchaRedemption, PendingSignup
from app.security import captcha
from tests.integration.conftest import TestSession

KEY = "integration-captcha-strong-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "captcha_enabled", True)
    monkeypatch.setattr(settings, "altcha_secret_key", KEY)


def proof(challenge=None, start=0):
    challenge = Challenge.from_dict(challenge or captcha.signup_challenge())
    solution = solve_challenge(challenge, counter_start=start)
    assert solution is not None
    return Payload(challenge, solution)


async def submit(client, path, email, value):
    if path == "api":
        return await client.post(
            "/api/v1/auth/signup", json={"email": email, "password": "long-enough", "altcha": value}
        )
    await client.get("/signup")
    return await client.post(
        "/signup",
        data={
            "email": email,
            "password": "long-enough",
            "password_confirm": "long-enough",
            "altcha": value or "",
            "csrf_token": client.cookies["csrf_token"],
        },
    )


@pytest.mark.parametrize("path", ["api", "html"])
async def test_missing_and_invalid_proofs_have_no_signup_effects(client, db, path):
    with patch("app.auth.router.hash_password") as api_hash, patch("app.frontend.auth.hash_password") as html_hash:
        for value in (None, "test", "e30="):
            response = await submit(client, path, "blocked@example.com", value)
            assert response.status_code == 422
        api_hash.assert_not_called()
        html_hash.assert_not_called()
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == 0
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 0


@pytest.mark.parametrize(("first", "second"), [("api", "html"), ("html", "api")])
async def test_real_challenge_single_use_across_both_signup_paths(client, db, first, second):
    response = await client.get("/api/v1/auth/captcha/challenge")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    solved = proof(response.json())
    accepted = await submit(client, first, "accepted@example.com", solved.to_base64())
    assert accepted.status_code == (201 if first == "api" else 200)
    # A different valid counter and reordered JSON must still consume exactly
    # the same signed challenge, even on the sibling handler.
    alternative = proof(response.json(), start=solved.solution.counter + 1)
    alias = base64.b64encode(json.dumps(alternative.to_dict(), sort_keys=True, indent=1).encode()).decode()
    rejected = await submit(client, second, "replayed@example.com", alias)
    assert rejected.status_code == 422
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == 1
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 1


async def test_concurrent_connections_allow_one_redemption_only(db):
    value = proof().to_base64()

    async def redeem():
        async with TestSession() as session:
            try:
                await captcha.require_signup_captcha(value, session)
                return 200
            except captcha.CaptchaError as error:
                return error.status_code

    results = await asyncio.gather(*(redeem() for _ in range(6)))
    assert results.count(200) == 1
    assert all(result in {200, 422, 503} for result in results)
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 1


async def test_live_advisory_gate_fails_closed_without_waiting(db):
    async with TestSession() as holder:
        await holder.execute(text("SELECT pg_advisory_xact_lock(1347635011,1128353876)"))
        with pytest.raises(captcha.CaptchaError) as error:
            await asyncio.wait_for(captcha.require_signup_captcha(proof().to_base64(), db), timeout=5)
        assert error.value.status_code == 503
        await holder.rollback()
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 0


async def test_capacity_cleanup_bound_and_live_entries_not_evicted(db, monkeypatch):
    monkeypatch.setattr(captcha, "LEDGER_CAPACITY", 3)
    now = datetime.now(UTC)
    db.add_all([CaptchaRedemption(signature=f"{i:064x}", expires_at=now + timedelta(minutes=5)) for i in range(3)])
    await db.commit()
    with pytest.raises(captcha.CaptchaError) as error:
        await captcha.require_signup_captcha(proof().to_base64(), db)
    assert error.value.status_code == 503
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 3
    await db.execute(text("UPDATE captcha_redemptions SET expires_at = now() - interval '1 second'"))
    await db.commit()
    await captcha.require_signup_captcha(proof().to_base64(), db)
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 1


@pytest.mark.parametrize("path", ["api", "html"])
async def test_commit_failure_blocks_every_signup_effect(client, db, path):
    with (
        patch("sqlalchemy.ext.asyncio.AsyncSession.commit", new=AsyncMock(side_effect=SQLAlchemyError("test"))),
        patch("app.auth.router.hash_password") as api_hash,
        patch("app.frontend.auth.hash_password") as html_hash,
    ):
        response = await submit(client, path, "blocked@example.com", proof().to_base64())
        assert response.status_code == 503
        api_hash.assert_not_called()
        html_hash.assert_not_called()
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == 0
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 0


async def test_durable_redemption_survives_failed_signup(client, db):
    solved = proof()
    with patch("app.auth.router.hash_password", side_effect=RuntimeError("signup failure")):
        with pytest.raises(RuntimeError):
            await submit(client, "api", "failed@example.com", solved.to_base64())
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 1
    rejected = await submit(client, "html", "failed@example.com", solved.to_base64())
    assert rejected.status_code == 422
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == 0


async def test_existing_mailbox_remains_neutral_but_also_consumes_fresh_proof(client, db):
    assert (await submit(client, "api", "existing@example.com", proof().to_base64())).status_code == 201
    duplicate = proof().to_base64()
    response = await submit(client, "api", "existing@example.com", duplicate)
    assert response.status_code == 201
    assert response.json() == {"message": "Signup received. Please verify your email."}
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == 1
    assert await db.scalar(select(func.count()).select_from(CaptchaRedemption)) == 2
    assert (await submit(client, "html", "existing@example.com", duplicate)).status_code == 422
