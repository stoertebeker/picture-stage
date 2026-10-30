"""Existing infinity/naive expiry values retain UTC access semantics (yk6g)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Gallery
from app.guest.service import is_gallery_expired


@pytest.mark.parametrize(
    ("expiry", "expired"),
    [
        (None, False),
        (datetime.max, False),
        (datetime.min, True),
        ((datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None), False),
        ((datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None), True),
    ],
)
def test_existing_naive_expiry_does_not_crash_or_mutate_record(expiry, expired):
    gallery = Gallery(expires_at=expiry)
    assert is_gallery_expired(gallery) is expired
    assert gallery.expires_at is expiry
