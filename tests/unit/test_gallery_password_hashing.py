"""Bounded gallery password hashing remains responsive under contention."""

import asyncio
import threading

import pytest
from fastapi import HTTPException

from app.auth.passwords import verify_password
from app.galleries import password_hashing


async def test_real_hash_remains_compatible() -> None:
    result = await password_hashing.hash_gallery_password("correct horse")
    assert verify_password("correct horse", result)
    assert not verify_password("wrong password", result)


async def test_capacity_and_cancelled_request_hold_slots_until_threads_finish(monkeypatch) -> None:
    release = threading.Event()
    started = 0
    started_lock = threading.Lock()

    def blocking_hash(value: str) -> str:
        nonlocal started
        with started_lock:
            started += 1
        if not release.wait(5):
            raise RuntimeError("test did not release the worker")
        return "hash:" + value

    monkeypatch.setattr(password_hashing, "hash_password", blocking_hash)
    first = asyncio.create_task(password_hashing.hash_gallery_password("one"))
    second = asyncio.create_task(password_hashing.hash_gallery_password("two"))
    try:
        for _ in range(200):
            if started == 2:
                break
            await asyncio.sleep(0.01)
        assert started == 2  # Reaching here proves the event loop remained free.
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(HTTPException) as rejected:
            await password_hashing.hash_gallery_password("three")
        assert rejected.value.status_code == 503
        assert started == 2
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    for _ in range(200):
        with password_hashing._lock:
            if password_hashing._running == 0:
                break
        await asyncio.sleep(0.01)
    assert await password_hashing.hash_gallery_password("next") == "hash:next"


async def test_worker_exception_releases_capacity(monkeypatch) -> None:
    def failing_hash(_value: str) -> str:
        raise ValueError("invalid password")

    monkeypatch.setattr(password_hashing, "hash_password", failing_hash)
    with pytest.raises(ValueError, match="invalid password"):
        await password_hashing.hash_gallery_password("invalid")
    with password_hashing._lock:
        assert password_hashing._running == 0
