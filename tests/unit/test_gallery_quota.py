"""DB-free unit tests for the per-user gallery quota helper (picture-stage-5gi)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.galleries.quota import GalleryQuotaExceeded, assert_within_gallery_quota


def _db_returning(count: int | None) -> MagicMock:
    """Fake AsyncSession whose scalar() resolves to the given gallery count."""
    db = MagicMock()
    db.scalar = AsyncMock(return_value=count)
    return db


async def test_unlimited_when_limit_zero_skips_count_query(monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 0)
    db = _db_returning(99)

    await assert_within_gallery_quota(uuid.uuid4(), db)

    db.scalar.assert_not_called()  # unlimited path must not even count


async def test_unlimited_when_limit_negative(monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", -1)
    db = _db_returning(99)

    await assert_within_gallery_quota(uuid.uuid4(), db)

    db.scalar.assert_not_called()


async def test_allows_creation_below_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 5)
    db = _db_returning(3)

    await assert_within_gallery_quota(uuid.uuid4(), db)  # no raise


async def test_rejects_creation_at_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 5)
    db = _db_returning(5)

    with pytest.raises(GalleryQuotaExceeded) as excinfo:
        await assert_within_gallery_quota(uuid.uuid4(), db)

    assert excinfo.value.limit == 5


async def test_rejects_creation_above_limit(monkeypatch):
    """A user already over a (lowered) limit cannot create more."""
    monkeypatch.setattr(settings, "max_galleries_per_user", 5)
    db = _db_returning(7)

    with pytest.raises(GalleryQuotaExceeded):
        await assert_within_gallery_quota(uuid.uuid4(), db)


async def test_none_count_treated_as_zero(monkeypatch):
    """Defensive: a NULL count (no rows) must not blow up the comparison."""
    monkeypatch.setattr(settings, "max_galleries_per_user", 5)
    db = _db_returning(None)

    await assert_within_gallery_quota(uuid.uuid4(), db)  # no raise
