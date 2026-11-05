"""A successful real logout revokes one credential across both auth paths."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth.revocation import REVOKED_TOKEN_CLEANUP_BATCH, cleanup_revoked_tokens
from app.auth.tokens import create_access_token, decode_access_token
from app.db.models import RevokedToken
from app.main import app
from app.security.middleware import _mint_csrf_token
from tests.unit.test_token_revocation import legacy_token, signature_alias


def logout_headers(token, source="cookie", *, cookie=None):
    session = token if source == "cookie" else cookie
    proof = _mint_csrf_token(session)
    headers = {"Cookie": f"csrf_token={proof}", "X-CSRF-Token": proof}
    if session is not None:
        headers["Cookie"] += f"; session={session}"
    if source == "bearer":
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def assert_authenticated(client, token, allowed):
    client.cookies.clear()
    bearer = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == (200 if allowed else 401), bearer.text
    client.cookies.clear()
    page = await client.get("/login", headers={"Cookie": f"session={token}"})
    assert page.status_code == (303 if allowed else 200), page.text
    if allowed:
        assert page.headers["location"] == "/dashboard"
    client.cookies.clear()


@pytest.mark.parametrize("path", ["/logout", "/api/v1/auth/logout"])
@pytest.mark.parametrize("source", ["cookie", "bearer"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_logout_replay_rejected_in_both_auth_paths_other_login_survives(client, db, owner, path, source, legacy):
    token = legacy_token(sub=str(owner.id)) if legacy else create_access_token(str(owner.id))
    second_login = create_access_token(str(owner.id))
    await assert_authenticated(client, token, True)
    await assert_authenticated(client, second_login, True)
    response = await client.post(path, headers=logout_headers(token, source))
    assert response.status_code == 303, response.text
    assert "Max-Age=0" in response.headers["set-cookie"]
    for replay in (token, token + "=", signature_alias(token)):
        await assert_authenticated(client, replay, False)
    await assert_authenticated(client, second_login, True)
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == 1
    await db.refresh(owner)
    assert owner.tokens_valid_after is None


@pytest.mark.parametrize("path", ["/logout", "/api/v1/auth/logout"])
@pytest.mark.parametrize("header_kind", ["different-token", "malformed", "padding-alias", "padbit-alias"])
async def test_credential_conflicts_are_side_effect_free(client, db, owner, path, header_kind):
    token = create_access_token(str(owner.id))
    second = create_access_token(str(owner.id))
    alternate = {
        "different-token": second,
        "malformed": "invalid",
        "padding-alias": token + "=",
        "padbit-alias": signature_alias(token),
    }[header_kind]
    response = await client.post(path, headers=logout_headers(alternate, "bearer", cookie=token))
    conflict = header_kind in {"different-token", "malformed"}
    assert response.status_code == (409 if conflict else 303)
    assert ("set-cookie" in response.headers) is not conflict
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == (0 if conflict else 1)
    await assert_authenticated(client, token, conflict)
    await assert_authenticated(client, second, True)


@pytest.mark.parametrize("path", ["/logout", "/api/v1/auth/logout"])
async def test_valid_bearer_with_invalid_cookie_revokes_only_bearer(client, db, owner, path):
    token = create_access_token(str(owner.id))
    response = await client.post(path, headers=logout_headers(token, "bearer", cookie="broken"))
    assert response.status_code == 303
    await assert_authenticated(client, token, False)
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == 1


async def test_parallel_logout_is_idempotent_and_visible_to_other_connections(db, owner):
    token = create_access_token(str(owner.id))

    async def logout():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/logout", headers=logout_headers(token))

    responses = await asyncio.gather(*(logout() for _ in range(8)))
    assert [response.status_code for response in responses] == [303] * 8
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == 1
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await assert_authenticated(client, token, False)


async def test_expiry_second_and_bounded_cleanup_keep_live_revocations(db, monkeypatch):
    # Freeze jose's clock inside exp's final accepted second.
    now = datetime(2026, 11, 5, 12, 0, 0, 500000, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("jose.jwt.datetime", FrozenDatetime)
    exp = int(now.timestamp())
    token = legacy_token(exp=exp)
    data = decode_access_token(token)
    assert data is not None
    db.add(RevokedToken(fingerprint=data.fingerprint, expires_at=data.expires_at))
    db.add_all(
        [
            RevokedToken(fingerprint=f"{number:064x}", expires_at=now - timedelta(days=1))
            for number in range(REVOKED_TOKEN_CLEANUP_BATCH + 5)
        ]
    )
    await db.commit()
    await cleanup_revoked_tokens(db, now)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == 6
    assert await db.get(RevokedToken, data.fingerprint) is not None
    now = datetime.fromtimestamp(exp + 1, UTC)
    assert decode_access_token(token) is None
    await cleanup_revoked_tokens(db, now)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == 0


async def test_admin_cutoff_still_invalidates_both_auth_paths(client, db, owner):
    token = create_access_token(str(owner.id))
    owner.tokens_valid_after = datetime.now(UTC) + timedelta(seconds=1)
    await db.commit()
    await assert_authenticated(client, token, False)
    assert await db.scalar(select(func.count()).select_from(RevokedToken)) == 0
