"""Unit tests for API-schema expiry future-validation (picture-stage-41j4).

Follow-up to the frontend `ath` fix: the JSON API (`POST/PATCH /api/v1/galleries`)
must also reject a non-future `expires_at`, so a client cannot create or update a
gallery into an already-dead state. The `_reject_past_expiry` field validator is
wired into both `GalleryCreate` and `GalleryUpdate`; it is pure (no DB) and
covered DB-free here via the Pydantic models directly.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.galleries.schemas import GalleryCreate, GalleryUpdate


def test_create_future_aware_expiry_accepted() -> None:
    future = datetime.now(UTC) + timedelta(days=7)
    model = GalleryCreate(name="Shoot", expires_at=future)
    assert model.expires_at == future


def test_create_future_naive_expiry_accepted_and_made_aware() -> None:
    future_naive = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    model = GalleryCreate(name="Shoot", expires_at=future_naive)
    assert model.expires_at is not None
    assert model.expires_at.tzinfo is not None  # naive input normalised to UTC
    assert model.expires_at > datetime.now(UTC)


def test_create_none_expiry_allowed() -> None:
    model = GalleryCreate(name="Shoot", expires_at=None)
    assert model.expires_at is None


def test_create_past_aware_expiry_rejected() -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(ValidationError):
        GalleryCreate(name="Shoot", expires_at=past)


def test_create_past_naive_expiry_rejected() -> None:
    past_naive = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
    with pytest.raises(ValidationError):
        GalleryCreate(name="Shoot", expires_at=past_naive)


def test_create_now_rejected_as_not_strictly_future() -> None:
    with pytest.raises(ValidationError):
        GalleryCreate(name="Shoot", expires_at=datetime.now(UTC))


def test_update_future_expiry_accepted() -> None:
    future = datetime.now(UTC) + timedelta(days=3)
    model = GalleryUpdate(expires_at=future)
    assert model.expires_at == future


def test_update_none_expiry_allowed() -> None:
    model = GalleryUpdate(expires_at=None)
    assert model.expires_at is None


def test_update_past_expiry_rejected() -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(ValidationError):
        GalleryUpdate(expires_at=past)


def test_update_past_naive_expiry_rejected() -> None:
    past_naive = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    with pytest.raises(ValidationError):
        GalleryUpdate(expires_at=past_naive)


@pytest.mark.parametrize("schema", [GalleryCreate, GalleryUpdate])
@pytest.mark.parametrize(
    "raw",
    [
        "9999-12-31T23:59:59.999999Z",
        "9999-12-31T23:59:59.999999",
        "9999-12-31T22:59:59.999999-01:00",
        "9999-12-31T23:59:59.999999-01:00",
        "0001-01-01T00:00:00+01:00",
    ],
)
def test_api_rejects_infinity_sentinel_and_utc_overflow(schema, raw) -> None:
    with pytest.raises(ValidationError, match="finite, representable UTC timestamp"):
        schema(name="Boundary proof", expires_at=raw)


@pytest.mark.parametrize("schema", [GalleryCreate, GalleryUpdate])
@pytest.mark.parametrize(
    "raw",
    [
        "9999-12-31T23:59:59.999998Z",
        "9999-12-31T23:59:59.999998",
        "9999-12-31T23:59:59.999999+02:00",
        "9999-12-31T22:59:59.999998-01:00",
    ],
)
def test_api_preserves_finite_boundary_precision_and_offsets(schema, raw) -> None:
    expected = datetime.fromisoformat(raw)
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=UTC)
    actual = schema(name="Boundary proof", expires_at=raw).expires_at
    assert actual == expected
    assert actual.microsecond == expected.microsecond
    assert actual.utcoffset() == expected.utcoffset()
