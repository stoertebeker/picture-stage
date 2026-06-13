"""Unit tests for gallery quota with per-user override (picture-stage-56k)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.galleries.quota import GalleryQuotaExceeded, assert_within_gallery_quota


@pytest.fixture
def owner_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def db_with_count():
    """Returns a factory: db_with_count(n) gives an AsyncSession mock reporting n galleries."""

    def _make(n: int) -> AsyncMock:
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=n)
        return db

    return _make


@pytest.mark.asyncio
async def test_global_limit_enforced(owner_id, db_with_count):
    db = db_with_count(5)
    with patch("app.galleries.quota.settings") as mock_settings:
        mock_settings.max_galleries_per_user = 5
        with pytest.raises(GalleryQuotaExceeded) as exc_info:
            await assert_within_gallery_quota(owner_id, db)
    assert exc_info.value.limit == 5


@pytest.mark.asyncio
async def test_override_takes_precedence_over_global(owner_id, db_with_count):
    db = db_with_count(3)
    with patch("app.galleries.quota.settings") as mock_settings:
        mock_settings.max_galleries_per_user = 5
        # User has override of 3 → quota exceeded even though global allows 5
        with pytest.raises(GalleryQuotaExceeded) as exc_info:
            await assert_within_gallery_quota(owner_id, db, limit_override=3)
    assert exc_info.value.limit == 3


@pytest.mark.asyncio
async def test_override_zero_means_unlimited(owner_id, db_with_count):
    db = db_with_count(999)
    with patch("app.galleries.quota.settings") as mock_settings:
        mock_settings.max_galleries_per_user = 5
        # override=0 means unlimited, no exception
        await assert_within_gallery_quota(owner_id, db, limit_override=0)
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_override_none_falls_back_to_global(owner_id, db_with_count):
    db = db_with_count(10)
    with patch("app.galleries.quota.settings") as mock_settings:
        mock_settings.max_galleries_per_user = 5
        # override=None → use global limit of 5, count=10 → exceeded
        with pytest.raises(GalleryQuotaExceeded):
            await assert_within_gallery_quota(owner_id, db, limit_override=None)


@pytest.mark.asyncio
async def test_global_zero_means_unlimited_without_override(owner_id, db_with_count):
    db = db_with_count(999)
    with patch("app.galleries.quota.settings") as mock_settings:
        mock_settings.max_galleries_per_user = 0
        await assert_within_gallery_quota(owner_id, db)
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_within_override_limit_no_exception(owner_id, db_with_count):
    db = db_with_count(2)
    with patch("app.galleries.quota.settings") as mock_settings:
        mock_settings.max_galleries_per_user = 5
        await assert_within_gallery_quota(owner_id, db, limit_override=10)
