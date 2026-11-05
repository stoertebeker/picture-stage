"""Every verification request advances a fixed window before any mailbox proof."""

import re
import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import lookup_progress as lp
from app.auth import token_lookup
from app.auth.passwords import hash_token, verify_token
from app.auth.tokens import generate_verification_token, verification_lookup_id
from app.db.models import PendingSignup, User
from app.main import app


def _pending(number, token, *, indexed=False, cleared=False):
    digest, salt = hash_token(token)
    return PendingSignup(
        id=number,
        email=f"pending-{number}@example.com",
        password_hash="unchanged-password",
        verification_token_hash=b"" if cleared else digest,
        verification_token_salt=b"" if cleared else salt,
        verification_lookup_id=verification_lookup_id(token) if indexed else None,
    )


async def _population(db, token, *, count=130, cleared=False):
    rows = [
        _pending(n, token if n == count else f"different-{n}", cleared=cleared and n < count)
        for n in range(1, count + 1)
    ]
    db.add_all(rows)
    await db.commit()
    return rows[-1]


def _path(route, token):
    return ("/api/v1/auth" if route == "api" else "") + f"/verify-email/{token}"


async def _request(client, route, token):
    return await (client.post(_path(route, token)) if route == "api" else client.get(_path(route, token)))


@pytest.mark.parametrize("route", ["api", "html"])
@pytest.mark.parametrize("shape", ["legacy", "v1", "collision"])
async def test_late_legacy_links_preserved_without_proof_before_final_window(client, db, verify_db, route, shape):
    token = "old-hash-only-link" if shape == "legacy" else generate_verification_token()
    target = await _population(db, token)
    if shape == "collision":
        wrong = _pending(0, "wrong-secret")
        wrong.verification_lookup_id = verification_lookup_id(token)
        db.add(wrong)
        await db.commit()
    statuses = []
    for _ in range(4):
        with patch.object(token_lookup, "verify_token", wraps=verify_token) as checks:
            response = await _request(client, route, token)
        statuses.append(response.status_code)
        assert checks.call_count <= (65 if response.status_code == 200 else 64)
        proof = await verify_db.scalar(select(PendingSignup.email_verified_at).where(PendingSignup.id == target.id))
        assert await verify_db.scalar(select(User.id)) is None
        if response.status_code != 503:
            assert response.status_code == 200 and proof is not None
            break
        assert proof is None
        assert response.headers[lp.RETRY_HEADER] == lp.RETRY_VALUE
        assert response.headers["Retry-After"] == "1"
        cookie = response.headers["set-cookie"]
        assert "verification_lookup=" in cookie and f"Path={_path(route, token)}" in cookie
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        if route == "html":
            assert "data-lookup-wait" in response.text
            assert "js/lookup-retry.js" in response.text
            script_sources = next(
                directive.strip().split()[1:]
                for directive in response.headers["content-security-policy"].split(";")
                if directive.strip().startswith("script-src ")
            )
            assert "'self'" in script_sources and "'unsafe-inline'" not in script_sources
            assert all("src=" in attributes for attributes in re.findall(r"<script\b([^>]*)>", response.text))
    assert statuses == [503, 503, 200]
    await db.refresh(target)
    assert target.verification_token_hash == target.verification_token_salt == b""
    assert target.verification_lookup_id is None
    assert target.password_hash == "unchanged-password"


@pytest.mark.parametrize("route", ["api", "html"])
@pytest.mark.parametrize("cleared", [False, True])
async def test_invalid_exhaustion_and_cleared_rows_advance_before_eligibility(client, db, route, cleared):
    await _population(db, "valid", cleared=cleared)
    for expected in (503, 503, 404):
        with patch.object(token_lookup, "verify_token", wraps=verify_token) as checks:
            response = await _request(client, route, "invalid")
        assert response.status_code == expected
        assert checks.call_count <= (1 if cleared else 64)
    assert await db.scalar(select(PendingSignup.id).where(PendingSignup.email_verified_at.isnot(None))) is None


@pytest.mark.parametrize("route", ["api", "html"])
async def test_indexed_lookup_still_verifies_one_candidate_and_consumes_under_lock(client, db, route):
    token = generate_verification_token()
    await _population(db, "legacy-target")
    db.add(_pending(1000, token, indexed=True))
    await db.commit()
    with patch.object(token_lookup, "verify_token", wraps=verify_token) as checks:
        assert (await _request(client, route, token)).status_code == 200
    assert checks.call_count == 2


def _cookie(token, *, purpose="verification", phase="l", key=None, expiry=None):
    progress = lp.LookupProgress(purpose, token)
    payload = f"{phase}.{key or f'{2**31 + 1000:032x}'}.{expiry or int(time.time()) + lp.LOOKUP_TTL}"
    return f"{payload}.{progress._mac(payload)}"


@pytest.mark.parametrize("kind", ["tamper", "expired", "oversize", "purpose", "token", "overflow", "phase"])
async def test_invalid_progress_restarts_without_skipping_or_overflow(client, db, kind):
    target = _pending(-(2**31), "valid")
    db.add(target)
    await db.commit()
    cookie = _cookie("valid")
    if kind == "tamper":
        cookie = cookie[:-1] + "z"
    elif kind == "expired":
        cookie = _cookie("valid", expiry=int(time.time()) - 1)
    elif kind == "oversize":
        cookie = "l." + "a" * 5000
    elif kind == "purpose":
        cookie = _cookie("valid", purpose="gallery")
    elif kind == "token":
        cookie = _cookie("different-token")
    elif kind == "overflow":
        cookie = _cookie("valid", key=f"{2**32:032x}")
    else:
        cookie = _cookie("valid", phase="h")
    client.cookies.set("verification_lookup", cookie, path="/verify-email/valid")
    assert (await client.get("/verify-email/valid")).status_code == 200


@pytest.mark.parametrize("ids", [[-(2**31), -1, 0, 2**31 - 1]])
async def test_imported_signed_int4_ids_are_continuable(client, db, monkeypatch, ids):
    monkeypatch.setattr(token_lookup, "LOOKUP_WINDOW", 1)
    db.add_all([_pending(number, "target" if number == ids[-1] else "other") for number in ids])
    await db.commit()
    for expected in (503, 503, 503, 200):
        assert (await client.get("/verify-email/target")).status_code == expected


@pytest.mark.parametrize("route", ["api", "html"])
async def test_root_path_cookie_continues_exact_request_route(db, route):
    await _population(db, "target", count=65)
    async with AsyncClient(transport=ASGITransport(app=app, root_path="/mount"), base_url="http://test") as client:
        path = "/mount" + _path(route, "target")
        request = client.post if route == "api" else client.get
        response = await request(path)
        assert response.status_code == 503
        assert f"Path={path}" in response.headers["set-cookie"]
        if route == "html":
            assert 'src="/mount/static/js/lookup-retry.js' in response.text
        assert (await request(path)).status_code == 200


@pytest.mark.parametrize("route", ["api", "html"])
@pytest.mark.parametrize("root", ["", "/mount"])
async def test_encoded_verification_url_continues_on_actual_cookie_path(db, verify_db, route, root):
    target = await _population(db, "target", count=65)
    async with AsyncClient(transport=ASGITransport(app=app, root_path=root), base_url="http://test") as client:
        path = root + _path(route, "%74arget")
        request = client.post if route == "api" else client.get
        response = await request(path)
        assert response.status_code == 503
        assert response.headers[lp.RETRY_HEADER] == lp.RETRY_VALUE
        assert f"Path={path}" in response.headers["set-cookie"]
        assert (
            await verify_db.scalar(select(PendingSignup.email_verified_at).where(PendingSignup.id == target.id)) is None
        )
        assert (await request(path)).status_code == 200
        assert (
            await verify_db.scalar(select(PendingSignup.email_verified_at).where(PendingSignup.id == target.id))
            is not None
        )


async def test_progress_renewal_requires_another_completed_window(client, db, monkeypatch):
    await _population(db, "target")
    now = time.time()
    monkeypatch.setattr(lp.time, "time", lambda: now)
    first = await client.get("/verify-email/target")
    first_value = first.cookies["verification_lookup"]
    monkeypatch.setattr(lp.time, "time", lambda: now + lp.LOOKUP_TTL - 1)
    second = await client.get("/verify-email/target")
    assert second.status_code == 503
    second_value = second.cookies["verification_lookup"]
    assert int(second_value.split(".")[2]) > int(first_value.split(".")[2])
    monkeypatch.setattr(lp.time, "time", lambda: now + lp.LOOKUP_TTL + 1)
    assert (await client.get("/verify-email/target")).status_code == 200
