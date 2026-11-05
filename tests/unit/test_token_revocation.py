"""JWT representation/claim boundaries and fail-closed logout transactions."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.dependencies import get_current_user, get_user_from_cookie
from app.auth.revocation import logout_credential
from app.auth.tokens import create_access_token, decode_access_token
from app.config import settings

USER_ID = "11111111-1111-1111-1111-111111111111"


def legacy_token(**claims):
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {"sub": USER_ID, "iat": now, "exp": now + 3600, "type": "access", **claims},
        settings.secret_key,
        algorithm="HS256",
    )


def signature_alias(token):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signing_input, signature = token.rsplit(".", 1)
    # SHA256's final base64 character has two unused bits; jose accepts them.
    altered = alphabet[alphabet.index(signature[-1]) ^ 1]
    return signing_input + "." + signature[:-1] + altered


def request(cookie=None, authorization=None):
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"session={cookie}".encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "POST", "path": "/logout", "headers": headers})


def test_new_tokens_are_distinct_even_with_frozen_time(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 5, 12, tzinfo=UTC)

    monkeypatch.setattr("app.auth.tokens.datetime", FrozenDatetime)
    tokens = {create_access_token(USER_ID) for _ in range(100)}
    assert len(tokens) == 100
    assert len({jwt.get_unverified_claims(token)["jti"] for token in tokens}) == 100


@pytest.mark.parametrize("alias", [lambda token: token + "=", signature_alias])
def test_signature_aliases_have_one_fingerprint(alias):
    token = legacy_token()
    original, alternate = decode_access_token(token), decode_access_token(alias(token))
    assert original is not None and alternate is not None
    assert original.fingerprint == alternate.fingerprint


@pytest.mark.parametrize("claim", ["iat", "exp"])
@pytest.mark.parametrize("value", [None, "0", "bad", [], {}, True, 1.25, 10**100, -(10**100)])
def test_malformed_numeric_claims_never_raise(claim, value):
    assert decode_access_token(legacy_token(**{claim: value})) is None


def test_exp_is_required_and_datetime_overflow_is_rejected():
    token = jwt.encode({"sub": USER_ID, "iat": 1, "type": "access"}, settings.secret_key, algorithm="HS256")
    assert decode_access_token(token) is None
    assert decode_access_token(legacy_token(exp=253402300799)) is None  # exp+1 overflows datetime


def test_legacy_jwt_still_decodes_and_retains_the_full_expiry_second():
    token = legacy_token()
    data = decode_access_token(token)
    assert data is not None and data.fingerprint is not None
    assert data.expires_at == datetime.fromtimestamp(jwt.get_unverified_claims(token)["exp"], UTC) + timedelta(
        seconds=1
    )
    assert "jti" not in jwt.get_unverified_claims(token)


@pytest.mark.parametrize("failure", ["cleanup", "insert", "commit"])
async def test_logout_database_failure_never_returns_cookie_deletion(failure):
    db = AsyncMock(spec=AsyncSession)
    if failure == "commit":
        db.commit.side_effect = SQLAlchemyError("test database failure")
    elif failure == "cleanup":
        db.execute.side_effect = SQLAlchemyError("test database failure")
    else:
        db.execute.side_effect = [None, SQLAlchemyError("test database failure")]
    with pytest.raises(SQLAlchemyError):
        await logout_credential(request(cookie=create_access_token(USER_ID)), db)
    if failure != "commit":
        db.commit.assert_not_awaited()


@pytest.mark.parametrize("cookie", [False, True])
async def test_auth_database_failure_does_not_authenticate(cookie):
    token = create_access_token(USER_ID)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = SQLAlchemyError("ledger unavailable")
    with pytest.raises(SQLAlchemyError):
        if cookie:
            await get_user_from_cookie(request(cookie=token), db)
        else:
            await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)


@pytest.mark.parametrize("authorization", ["Basic wrong", "Bearer", "Bearer invalid", "", "Bearer a b"])
async def test_explicit_invalid_header_does_not_fall_back_to_valid_cookie(authorization):
    db = AsyncMock(spec=AsyncSession)
    with pytest.raises(HTTPException) as error:
        await logout_credential(request(cookie=create_access_token(USER_ID), authorization=authorization), db)
    assert error.value.status_code == 409
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("token", [None, "broken", legacy_token(exp=1)])
async def test_missing_invalid_and_expired_credentials_only_delete_local_cookie(token):
    db = AsyncMock(spec=AsyncSession)
    response = await logout_credential(request(cookie=token), db)
    assert response.status_code == 303
    assert "Max-Age=0" in response.headers["set-cookie"]
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_logout_commits_before_constructing_success(monkeypatch):
    db = AsyncMock(spec=AsyncSession)
    events = []

    async def commit():
        events.append("committed")

    db.commit.side_effect = commit
    from app.auth import revocation

    original_response = revocation.RedirectResponse

    def response(*args, **kwargs):
        assert events == ["committed"]
        return original_response(*args, **kwargs)

    monkeypatch.setattr(revocation, "RedirectResponse", response)
    assert (await logout_credential(request(cookie=create_access_token(USER_ID)), db)).status_code == 303
