"""Calendar-date expiry uses the complete UTC day (GitHub #6)."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from starlette.requests import Request

from app.db.models import Gallery, GalleryStatus, User
from app.frontend import galleries as frontend
from app.guest import service as guest_service


def _freeze_now(monkeypatch, instant):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(frontend, "datetime", FrozenDateTime)
    monkeypatch.setattr(guest_service, "datetime", FrozenDateTime)


@pytest.mark.parametrize("raw", ["2030-10-30", "2030-10-31", "2032-02-29", "9999-12-30"])
def test_date_expires_at_final_microsecond_utc(monkeypatch, raw):
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 12, tzinfo=UTC))
    expires_at = frontend._validate_future_expiry(raw)
    assert expires_at == datetime.fromisoformat(f"{raw}T23:59:59.999999+00:00")
    assert expires_at.tzinfo is UTC


@pytest.mark.parametrize("raw", ["2031-03-30", "2031-10-26"])
def test_browser_daylight_saving_dates_do_not_change_utc_boundary(monkeypatch, raw):
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 12, tzinfo=UTC))
    assert frontend._validate_future_expiry(raw).isoformat() == f"{raw}T23:59:59.999999+00:00"


@pytest.mark.parametrize(
    "now", [datetime(2030, 10, 30, 0, tzinfo=UTC), datetime(2030, 10, 30, 23, 59, 59, 999998, tzinfo=UTC)]
)
def test_today_remains_available_until_its_utc_end(monkeypatch, now):
    _freeze_now(monkeypatch, now)
    expires_at = frontend._validate_future_expiry("2030-10-30")
    assert expires_at > now
    assert not guest_service.is_gallery_expired(Gallery(expires_at=expires_at))


@pytest.mark.parametrize(
    "now", [datetime(2030, 10, 30, 23, 59, 59, 999999, tzinfo=UTC), datetime(2030, 10, 31, 0, tzinfo=UTC)]
)
def test_date_not_in_the_future_is_rejected(monkeypatch, now):
    _freeze_now(monkeypatch, now)
    with pytest.raises(frontend._ExpiryInPastError):
        frontend._validate_future_expiry("2030-10-30")


def test_gallery_is_expired_at_following_utc_midnight(monkeypatch):
    expiry = datetime(2030, 10, 30, 23, 59, 59, 999999, tzinfo=UTC)
    _freeze_now(monkeypatch, datetime(2030, 10, 31, 0, tzinfo=UTC))
    assert guest_service.is_gallery_expired(Gallery(expires_at=expiry))


@pytest.mark.parametrize("raw", ["2030-10-29", "2000-01-01"])
def test_past_calendar_dates_rejected(monkeypatch, raw):
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 12, tzinfo=UTC))
    with pytest.raises(frontend._ExpiryInPastError):
        frontend._validate_future_expiry(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-date",
        "9999-12-31",
        "2030-02-30",
        "20301031",
        "2030-W44-4",
        "2030-10-31T12:00",
        "2030-10-31T12:00:00+02:00",
        "",
        " 2030-10-31",
    ],
)
def test_only_canonical_calendar_dates_accepted(raw):
    with pytest.raises(ValueError):
        frontend._validate_future_expiry(raw)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2030, 10, 31, 0, 30, tzinfo=timezone(timedelta(hours=2))), date(2030, 10, 30)),
        (datetime(2030, 10, 30, 23, 30, tzinfo=timezone(timedelta(hours=-4))), date(2030, 10, 31)),
        (datetime(2030, 10, 30, 12), date(2030, 10, 30)),
        (None, None),
    ],
)
def test_detail_context_displays_utc_date_without_mutating_existing_instant(instant, expected):
    gallery = Gallery(status=GalleryStatus.draft, expires_at=instant)
    request = Request({"type": "http", "path": "/galleries/test", "headers": []})
    context = frontend._build_context(request, gallery, [], User())
    assert context["expiry_date"] == expected
    assert gallery.expires_at is instant
