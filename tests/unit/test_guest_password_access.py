"""Unit tests for the shared password-protected guest read boundary."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.guest.service import require_gallery_read_access


async def test_passwordless_gallery_allows_token_only_reads() -> None:
    gallery = SimpleNamespace(id=uuid.uuid4(), password_hash=None)
    db = AsyncMock()

    await require_gallery_read_access(gallery, None, db)  # type: ignore[arg-type]

    db.execute.assert_not_awaited()


async def test_protected_gallery_rejects_missing_session_before_query() -> None:
    gallery = SimpleNamespace(id=uuid.uuid4(), password_hash="configured")
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await require_gallery_read_access(gallery, None, db)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "No active session"
    db.execute.assert_not_awaited()


async def test_protected_gallery_rejects_session_not_bound_to_gallery() -> None:
    gallery = SimpleNamespace(id=uuid.uuid4(), password_hash="configured")
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    with pytest.raises(HTTPException) as exc_info:
        await require_gallery_read_access(gallery, uuid.uuid4(), db)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "No active session"
    db.execute.assert_awaited_once()


async def test_protected_gallery_accepts_bound_session() -> None:
    gallery = SimpleNamespace(id=uuid.uuid4(), password_hash="configured")
    session_id = uuid.uuid4()
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = session_id
    db.execute.return_value = result

    await require_gallery_read_access(gallery, session_id, db)  # type: ignore[arg-type]

    db.execute.assert_awaited_once()
