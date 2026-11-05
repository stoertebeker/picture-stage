"""The read guard uses fresh authorization state before the public fast path."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.guest import service


@pytest.fixture
def state(monkeypatch):
    gallery = SimpleNamespace(id=uuid.uuid4(), password_hash=None, authorization_epoch=2)
    db = AsyncMock()
    loader = AsyncMock(return_value=gallery)
    monkeypatch.setattr(service, "lock_gallery_for_guest_read", loader)
    return gallery, db, loader


async def test_passwordless_gallery_allows_token_only_reads(state) -> None:
    gallery, db, loader = state
    result = await service.require_gallery_read_access(gallery, None, db, token="token")
    assert result is gallery
    loader.assert_awaited_once_with("token", gallery.id, db)
    db.scalar.assert_not_awaited()


async def test_protected_gallery_rejects_missing_session_before_session_query(state) -> None:
    gallery, db, _ = state
    gallery.password_hash = "configured"
    with pytest.raises(HTTPException) as rejected:
        await service.require_gallery_read_access(gallery, None, db, token="token")
    assert rejected.value.status_code == 403
    db.scalar.assert_not_awaited()


async def test_stale_passwordless_state_cannot_bypass_new_password(state) -> None:
    gallery, db, _ = state
    stale = SimpleNamespace(id=gallery.id, password_hash=None, authorization_epoch=1)
    gallery.password_hash = "newly-configured"
    with pytest.raises(HTTPException) as rejected:
        await service.require_gallery_read_access(stale, None, db, token="token")
    assert rejected.value.status_code == 403


async def test_protected_gallery_rejects_missing_current_session(state) -> None:
    gallery, db, _ = state
    gallery.password_hash = "configured"
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as rejected:
        await service.require_gallery_read_access(gallery, uuid.uuid4(), db, token="token")
    assert rejected.value.status_code == 403
    db.scalar.assert_awaited_once()


async def test_protected_gallery_accepts_current_session(state) -> None:
    gallery, db, _ = state
    gallery.password_hash = "configured"
    session_id = uuid.uuid4()
    db.scalar.return_value = SimpleNamespace(id=session_id, authorization_epoch=2)
    assert await service.require_gallery_read_access(gallery, session_id, db, token="token") is gallery
    db.scalar.assert_awaited_once()
