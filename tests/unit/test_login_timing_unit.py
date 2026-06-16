"""DB-free behavioral tests for the login timing-equalization guard (picture-stage-0y7).

Calls the login handlers directly with a mocked AsyncSession, so the runtime
behavior is verified locally without PostgreSQL. Asserts the core security
contract: BOTH the missing-account and the wrong-password path run exactly one
bcrypt verify, so response time can't reveal whether an email is registered
(account enumeration). For a missing user, the verify runs against the module's
dummy hash. The full HTTP flow / functional 401-vs-token behavior is covered by
the other auth tests.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth.router import _DUMMY_PASSWORD_HASH
from app.auth.router import login as api_login
from app.auth.router import login_form as api_login_form
from app.auth.schemas import LoginRequest
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


@pytest.mark.asyncio
async def test_api_login_missing_user_still_runs_bcrypt_against_dummy() -> None:
    """Missing account must still spend one bcrypt verify (against the dummy hash)."""
    db = _mock_db(user=None)
    body = LoginRequest(email="ghost@example.com", password="whatever-long-password")

    with patch("app.auth.router.verify_password", return_value=False) as vp:
        with pytest.raises(HTTPException) as exc:
            await api_login(MagicMock(), body, db)

    assert exc.value.status_code == 401
    vp.assert_called_once()
    # The verify ran against the module-level dummy hash, not a real user hash.
    assert vp.call_args.args[1] == _DUMMY_PASSWORD_HASH


@pytest.mark.asyncio
async def test_api_login_wrong_password_runs_bcrypt_against_user() -> None:
    """Existing account + wrong password runs one bcrypt verify against the user hash."""
    user = _user_with_hash()
    db = _mock_db(user=user)
    body = LoginRequest(email="real@example.com", password="wrong-long-password")

    with patch("app.auth.router.verify_password", return_value=False) as vp:
        with pytest.raises(HTTPException) as exc:
            await api_login(MagicMock(), body, db)

    assert exc.value.status_code == 401
    vp.assert_called_once()
    assert vp.call_args.args[1] == user.password_hash


def _form_request(email: str, password: str) -> MagicMock:
    request = MagicMock()
    request.form = AsyncMock(return_value={"email": email, "password": password})
    return request


@pytest.mark.asyncio
async def test_form_login_missing_user_still_runs_bcrypt_against_dummy() -> None:
    db = _mock_db(user=None)
    request = _form_request("ghost@example.com", "whatever-long-password")

    with patch("app.auth.router.verify_password", return_value=False) as vp:
        with pytest.raises(HTTPException) as exc:
            await api_login_form(request, db)

    assert exc.value.status_code == 401
    vp.assert_called_once()
    assert vp.call_args.args[1] == _DUMMY_PASSWORD_HASH


@pytest.mark.asyncio
async def test_form_login_wrong_password_runs_bcrypt_against_user() -> None:
    user = _user_with_hash()
    db = _mock_db(user=user)
    request = _form_request("real@example.com", "wrong-long-password")

    with patch("app.auth.router.verify_password", return_value=False) as vp:
        with pytest.raises(HTTPException) as exc:
            await api_login_form(request, db)

    assert exc.value.status_code == 401
    vp.assert_called_once()
    assert vp.call_args.args[1] == user.password_hash
