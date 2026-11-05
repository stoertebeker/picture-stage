"""Legacy scan capacity and token format contracts."""

import asyncio

import pytest
from fastapi import HTTPException

from app.auth import token_lookup
from app.auth.tokens import generate_verification_token, verification_lookup_id


def test_new_verification_token_has_random_url_safe_secret_and_locator():
    first, second = generate_verification_token(), generate_verification_token()
    assert first != second
    assert verification_lookup_id(first) is not None
    assert verification_lookup_id(first) != verification_lookup_id(second)
    assert verification_lookup_id("old-opaque-token") is None
    assert verification_lookup_id("v1.bad.secret") is None


async def test_legacy_scan_permit_is_released_on_cancellation(monkeypatch):
    monkeypatch.setattr(token_lookup, "_MAX_LEGACY_SCANS", 1)
    started = asyncio.Event()
    never = asyncio.Event()

    async def running():
        async with token_lookup.legacy_token_scan("gallery"):
            started.set()
            await never.wait()

    task = asyncio.create_task(running())
    await asyncio.wait_for(started.wait(), 1)
    try:
        with pytest.raises(HTTPException) as busy:
            async with token_lookup.legacy_token_scan("gallery"):
                pytest.fail("capacity was not enforced")
        assert busy.value.status_code == 503
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with token_lookup.legacy_token_scan("gallery"):
        pass
    assert token_lookup._legacy_running == {"gallery": 0, "verification": 0}


async def test_cancellation_of_pending_signup_window_releases_permit():
    started = asyncio.Event()
    never = asyncio.Event()

    class DB:
        async def scalars(self, query):
            assert query._limit_clause.value == 65
            started.set()
            await never.wait()

    db = DB()
    task = asyncio.create_task(token_lookup.resolve_pending_signup("old-opaque-token", db))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert token_lookup._legacy_running["verification"] == 0
