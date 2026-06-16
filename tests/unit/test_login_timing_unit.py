"""DB-free behavioral tests for the login timing-equalization guard (picture-stage-0y7).

Both surviving login paths — the JSON API ``login`` and the UI form ``login_submit``
— must always run exactly one bcrypt verify, so response time can't reveal whether
an email is registered (account enumeration). The equalization lives in the shared
helper ``verify_password_or_dummy`` (app/auth/passwords.py); these tests assert the
helper runs bcrypt even for a missing user AND that both handlers invoke it
unconditionally (also when the user lookup returns None).

The retired ``/api/v1/auth/login-form`` endpoint (dead code, CSRF-exempt cookie
setter, removed in picture-stage-6bs) is intentionally no longer covered.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth import passwords
from app.auth.router import login as api_login
from app.auth.schemas import LoginRequest
from app.frontend.auth import login_submit
from app.security.rate_limit import limiter


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """slowapi's @limiter.limit decorator expects ASGI app state; disable it so the
    handler can be invoked directly. Restored afterwards to not leak into other tests."""
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


def _mock_db(user: object | None) -> MagicMock:
    """AsyncSession mock whose single execute() resolves to the given user (or None)."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)
    return db


def _user_with_hash() -> MagicMock:
    user = MagicMock()
    user.password_hash = "$2b$12$some.real.looking.bcrypt.hash.value.placeholder.xxxxxx"
    return user


# --- The shared helper: always one bcrypt verify; dummy hash when user missing ---


def test_helper_runs_bcrypt_even_when_hash_is_none() -> None:
    with patch("app.auth.passwords.verify_password", return_value=False) as vp:
        result = passwords.verify_password_or_dummy("pw", None)
    assert result is False
    vp.assert_called_once()
    # bcrypt ran against the module-level dummy hash, not skipped.
    assert vp.call_args.args[1] == passwords._DUMMY_PASSWORD_HASH


def test_helper_verifies_against_real_hash() -> None:
    with patch("app.auth.passwords.verify_password", return_value=True) as vp:
        result = passwords.verify_password_or_dummy("pw", "real-hash")
    assert result is True
    vp.assert_called_once_with("pw", "real-hash")


# --- API login (JSON): helper invoked unconditionally, even for a missing user ---


@pytest.mark.asyncio
async def test_api_login_missing_user_still_runs_verify() -> None:
    db = _mock_db(user=None)
    body = LoginRequest(email="ghost@example.com", password="whatever-long-password")

    with patch("app.auth.router.verify_password_or_dummy", return_value=False) as vp:
        with pytest.raises(HTTPException) as exc:
            await api_login(MagicMock(), body, db)

    assert exc.value.status_code == 401
    vp.assert_called_once()
    assert vp.call_args.args[1] is None  # no user -> None hash -> helper uses dummy


@pytest.mark.asyncio
async def test_api_login_wrong_password_runs_verify_against_user() -> None:
    user = _user_with_hash()
    db = _mock_db(user=user)
    body = LoginRequest(email="real@example.com", password="wrong-long-password")

    with patch("app.auth.router.verify_password_or_dummy", return_value=False) as vp:
        with pytest.raises(HTTPException) as exc:
            await api_login(MagicMock(), body, db)

    assert exc.value.status_code == 401
    vp.assert_called_once()
    assert vp.call_args.args[1] == user.password_hash


# --- Frontend login_submit (the UI path): same guarantee, renders 401 template ---


def _form_request(email: str, password: str) -> MagicMock:
    request = MagicMock()
    request.form = AsyncMock(return_value={"email": email, "password": password})
    request.cookies = {}
    return request


@pytest.mark.asyncio
async def test_form_login_missing_user_still_runs_verify() -> None:
    db = _mock_db(user=None)
    request = _form_request("ghost@example.com", "whatever-long-password")

    with (
        patch("app.frontend.auth.verify_password_or_dummy", return_value=False) as vp,
        patch("app.frontend.auth.templates") as tmpl,
    ):
        await login_submit(request, db)

    vp.assert_called_once()
    assert vp.call_args.args[1] is None
    assert tmpl.TemplateResponse.call_args.kwargs.get("status_code") == 401


@pytest.mark.asyncio
async def test_form_login_wrong_password_runs_verify_against_user() -> None:
    user = _user_with_hash()
    db = _mock_db(user=user)
    request = _form_request("real@example.com", "wrong-long-password")

    with (
        patch("app.frontend.auth.verify_password_or_dummy", return_value=False) as vp,
        patch("app.frontend.auth.templates") as tmpl,
    ):
        await login_submit(request, db)

    vp.assert_called_once()
    assert vp.call_args.args[1] == user.password_hash
    assert tmpl.TemplateResponse.call_args.kwargs.get("status_code") == 401
