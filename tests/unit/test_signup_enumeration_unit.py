"""DB-free behavioral tests for the signup account-enumeration guard (picture-stage-42q).

Calls the signup handlers directly with a mocked AsyncSession, so the runtime
behavior is verified locally without PostgreSQL (the full HTTP flow is covered
by tests/integration/test_signup_enumeration.py in CI). Asserts the core
security contract: an existing email yields a neutral response and triggers
NO insert, NO commit, and NO admin notification.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.router import signup as api_signup
from app.auth.schemas import SignupRequest
from app.security.rate_limit import limiter

NEUTRAL_MESSAGE = "Signup received. Please verify your email."


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """slowapi's @limiter.limit decorator expects ASGI app state; disable it so the
    handler can be invoked directly. Restored afterwards to not leak into other tests."""
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


def _mock_db(user_exists: bool, pending_exists: bool) -> MagicMock:
    """AsyncSession mock: first execute() -> user lookup, second -> pending lookup."""
    db = MagicMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = object() if user_exists else None
    pending_result = MagicMock()
    pending_result.scalar_one_or_none.return_value = object() if pending_exists else None
    db.execute = AsyncMock(side_effect=[user_result, pending_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_exists", "pending_exists"),
    [(True, False), (False, True), (True, True)],
)
async def test_api_existing_email_is_neutral_and_inert(user_exists: bool, pending_exists: bool) -> None:
    db = _mock_db(user_exists=user_exists, pending_exists=pending_exists)
    body = SignupRequest(email="taken@example.com", password="valid-long-password")

    with patch("app.auth.router.notify_admins_signup", new=AsyncMock()) as notify:
        resp = await api_signup(MagicMock(), body, db)

    # Same neutral response as a fresh signup, no token field on the model.
    assert resp.message == NEUTRAL_MESSAGE
    assert "verification_token" not in type(resp).model_fields
    # No state change and no admin alert for an already-known email.
    db.add.assert_not_called()
    db.commit.assert_not_called()
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_api_fresh_email_creates_pending_and_notifies() -> None:
    db = _mock_db(user_exists=False, pending_exists=False)
    body = SignupRequest(email="fresh@example.com", password="valid-long-password")

    with patch("app.auth.router.notify_admins_signup", new=AsyncMock()) as notify:
        resp = await api_signup(MagicMock(), body, db)

    assert resp.message == NEUTRAL_MESSAGE
    # A fresh signup DOES persist a PendingSignup and notify admins.
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_short_password_rejected_before_db_lookup() -> None:
    """The length guard fires first and leaks nothing about account existence."""
    from fastapi import HTTPException

    db = _mock_db(user_exists=True, pending_exists=False)
    body = SignupRequest(email="taken@example.com", password="short")

    with pytest.raises(HTTPException) as exc:
        await api_signup(MagicMock(), body, db)

    assert exc.value.status_code == 422
    db.execute.assert_not_called()  # rejected before any existence lookup
    db.add.assert_not_called()
