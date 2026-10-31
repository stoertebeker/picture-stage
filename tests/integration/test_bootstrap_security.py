"""First admin bootstrap requires an operator token and one atomic claim."""

import asyncio
import re
import secrets
from unittest.mock import patch

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.auth.passwords import verify_password
from app.config import settings
from app.db.models import SetupState, User, UserStatus
from app.frontend import setup


@pytest.fixture
def bootstrap_token(monkeypatch):
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(settings, "bootstrap_token", SecretStr(token))
    return token


@pytest.fixture
async def fresh_setup(db):
    db.add(SetupState(id=1, completed=False))
    await db.commit()


def _form(csrf, bootstrap, email="admin@example.com"):
    return {
        "csrf_token": csrf,
        "bootstrap_token": bootstrap,
        "email": email,
        "password": "password-for-test",
        "password_confirm": "password-for-test",
    }


async def test_fresh_setup_requires_token_and_consumes_it_atomically(client, db, fresh_setup, bootstrap_token):
    page = await client.get("/setup")
    assert page.status_code == 200
    assert bootstrap_token not in page.text
    csrf = page.cookies["csrf_token"]
    assert re.search(rf'name="csrf_token" value="{re.escape(csrf)}"', page.text)

    for supplied in ("", "wrong", secrets.token_urlsafe(32)):
        response = await client.post("/setup", data=_form(csrf, supplied))
        assert response.status_code == 403
        if supplied:
            assert supplied not in response.text
        assert await db.scalar(select(func.count()).select_from(User)) == 0

    response = await client.post("/setup", data=_form(csrf, bootstrap_token))
    assert response.status_code == 303 and response.headers["location"] == "/login"
    db.expire_all()
    admin = await db.scalar(select(User))
    assert admin is not None and admin.status == UserStatus.admin and admin.email_verified_at is not None
    assert verify_password("password-for-test", admin.password_hash)
    assert (await db.get(SetupState, 1)).completed is True
    assert (await client.post("/setup", data=_form(csrf, bootstrap_token))).status_code == 404


@pytest.mark.parametrize("configured", ["", "CHANGE_ME", "A" * 43, "x" * 500, "abcä" * 20])
async def test_missing_or_weak_configuration_denies_setup(client, db, fresh_setup, monkeypatch, configured):
    monkeypatch.setattr(settings, "bootstrap_token", SecretStr(configured))
    page = await client.get("/setup")
    assert page.status_code == 503
    assert 'name="bootstrap_token"' not in page.text
    csrf = page.cookies["csrf_token"]
    result = await client.post("/setup", data=_form(csrf, configured))
    assert result.status_code == 503
    assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_existing_installation_and_consumed_state_do_not_reopen(client, db, bootstrap_token):
    db.add_all(
        [SetupState(id=1, completed=True), User(email="old@example.com", password_hash="x", status=UserStatus.admin)]
    )
    await db.commit()
    assert (await client.get("/setup")).status_code == 404
    await db.execute(User.__table__.delete())
    await db.commit()
    assert (await client.get("/setup")).status_code == 404


async def test_missing_singleton_fails_closed(client, db, bootstrap_token):
    assert (await client.get("/setup")).status_code == 503
    assert await db.scalar(select(func.count()).select_from(User)) == 0


async def test_failed_insert_rolls_back_claim(client, db, fresh_setup, bootstrap_token):
    page = await client.get("/setup")
    csrf = page.cookies["csrf_token"]
    with patch.object(setup, "hash_password", side_effect=RuntimeError("simulated failure")):
        with pytest.raises(RuntimeError, match="simulated failure"):
            await client.post("/setup", data=_form(csrf, bootstrap_token))
    db.expire_all()
    assert not (await db.get(SetupState, 1)).completed
    assert await db.scalar(select(func.count()).select_from(User)) == 0
    assert (await client.post("/setup", data=_form(csrf, bootstrap_token))).status_code == 303


async def test_concurrent_setup_commits_only_one_admin(client, db, fresh_setup, bootstrap_token, monkeypatch):
    page = await client.get("/setup")
    csrf = page.cookies["csrf_token"]
    original = setup._has_users
    arrived = 0
    both_checked = asyncio.Event()

    async def synchronized_check(session):
        nonlocal arrived
        result = await original(session)
        if arrived < 2:
            arrived += 1
            if arrived == 2:
                both_checked.set()
            await asyncio.wait_for(both_checked.wait(), timeout=5)
        return result

    monkeypatch.setattr(setup, "_has_users", synchronized_check)
    first, second = await asyncio.gather(
        client.post("/setup", data=_form(csrf, bootstrap_token, "one@example.com")),
        client.post("/setup", data=_form(csrf, bootstrap_token, "two@example.com")),
    )
    assert sorted([first.status_code, second.status_code]) == [303, 404]
    db.expire_all()
    assert await db.scalar(select(func.count()).select_from(User).where(User.status == UserStatus.admin)) == 1
    assert (await db.get(SetupState, 1)).completed is True
