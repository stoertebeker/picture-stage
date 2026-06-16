"""Unit tests for gallery expiry future-validation (picture-stage-ath).

`_validate_future_expiry` parses the ISO string from the expiry form, treats a
naive value as UTC, and rejects any instant that is not strictly in the future —
so a photographer can no longer silently kill a share link by setting a past
date. The helper is pure (no DB), so it is covered DB-free here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.frontend.galleries import _ExpiryInPastError, _validate_future_expiry


def test_future_naive_datetime_accepted_and_made_aware() -> None:
    future = datetime.now(UTC) + timedelta(days=7)
    # Naive ISO string (no offset), as emitted by <input type="datetime-local">.
    raw = future.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M")
    result = _validate_future_expiry(raw)
    assert result.tzinfo is not None  # naive input normalised to UTC
    assert result > datetime.now(UTC)


def test_future_aware_datetime_accepted() -> None:
    future = datetime.now(UTC) + timedelta(days=1)
    result = _validate_future_expiry(future.isoformat())
    assert result > datetime.now(UTC)


def test_past_naive_datetime_rejected() -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    raw = past.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M")
    with pytest.raises(_ExpiryInPastError):
        _validate_future_expiry(raw)


def test_past_aware_datetime_rejected() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(_ExpiryInPastError):
        _validate_future_expiry(past.isoformat())


def test_now_is_rejected_as_not_strictly_future() -> None:
    # An instant at/just-before "now" is effectively already expired.
    with pytest.raises(_ExpiryInPastError):
        _validate_future_expiry(datetime.now(UTC).isoformat())


def test_malformed_input_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        _validate_future_expiry("not-a-date")
