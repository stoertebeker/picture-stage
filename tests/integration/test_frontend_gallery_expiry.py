"""Date-only frontend expiry persists the whole UTC day while API times stay precise."""

import json
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

from app.auth.passwords import hash_token
from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus
from app.frontend import galleries as frontend
from app.guest import service as guest_service
from app.security.middleware import _mint_csrf_token


class _Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inputs = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            attributes = dict(attrs)
            self.inputs[attributes.get("name")] = attributes


def _freeze_now(monkeypatch, instant):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(frontend, "datetime", FrozenDateTime)
    monkeypatch.setattr(guest_service, "datetime", FrozenDateTime)


def _authenticate(client, owner, locale="de"):
    token = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    client.cookies.set("lang", locale)
    return csrf


async def _gallery(db, owner, expires_at=None):
    token = "expiry-date-proof"
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="UTC expiry proof",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        expires_at=expires_at,
    )
    db.add(gallery)
    await db.commit()
    return gallery, f"/g/{token}"


@pytest.mark.parametrize("locale", ["de", "en"])
async def test_today_is_date_only_and_accessible_through_utc_day(client, db, owner, verify_db, monkeypatch, locale):
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 12, tzinfo=UTC))
    gallery, guest_path = await _gallery(db, owner)
    csrf = _authenticate(client, owner, locale)
    response = await client.post(
        f"/galleries/{gallery.id}/expiry", data={"expires_at": "2030-10-30", "csrf_token": csrf}
    )
    assert response.status_code == 200
    expiry_input = _Inputs(response.text).inputs["expires_at"]
    assert expiry_input["type"] == "date"
    assert expiry_input["max"] == "9999-12-30"
    assert expiry_input["value"] == "2030-10-30"
    assert "(UTC)" in response.text
    saved = await verify_db.get(Gallery, gallery.id)
    assert saved.expires_at == datetime(2030, 10, 30, 23, 59, 59, 999999, tzinfo=UTC)
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 23, 59, 59, 999998, tzinfo=UTC))
    assert (await client.get(guest_path)).status_code == 200
    _freeze_now(monkeypatch, datetime(2030, 10, 31, 0, tzinfo=UTC))
    assert (await client.get(guest_path)).status_code == 410


@pytest.mark.parametrize("raw", ["2030-10-31", "2031-03-30", "2031-10-26", "9999-12-30"])
async def test_future_dates_save_without_timezone_or_day_overflow(client, db, owner, verify_db, monkeypatch, raw):
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 12, tzinfo=UTC))
    gallery, _ = await _gallery(db, owner)
    csrf = _authenticate(client, owner)
    response = await client.post(f"/galleries/{gallery.id}/expiry", data={"expires_at": raw, "csrf_token": csrf})
    assert response.status_code == 200
    assert (await verify_db.get(Gallery, gallery.id)).expires_at == datetime.fromisoformat(
        f"{raw}T23:59:59.999999+00:00"
    )


async def test_existing_timestamp_display_and_empty_submit_preserve_instant_then_explicit_clear(
    client, db, owner, verify_db
):
    # Its UTC date is the preceding day; display must never copy the offset-local date.
    instant = datetime(2031, 1, 2, 0, 30, 15, 123456, tzinfo=timezone(timedelta(hours=2)))
    gallery, _ = await _gallery(db, owner, instant)
    csrf = _authenticate(client, owner)
    detail = await client.get(f"/galleries/{gallery.id}")
    assert detail.status_code == 200
    assert _Inputs(detail.text).inputs["expires_at"]["value"] == "2031-01-01"
    assert (await verify_db.get(Gallery, gallery.id)).expires_at == instant
    blank = await client.post(f"/galleries/{gallery.id}/expiry", data={"expires_at": "", "csrf_token": csrf})
    assert blank.status_code == 200
    await verify_db.rollback()
    assert (await verify_db.get(Gallery, gallery.id)).expires_at == instant
    removed = await client.post(
        f"/galleries/{gallery.id}/expiry", data={"clear_expiry": "1", "expires_at": "", "csrf_token": csrf}
    )
    assert removed.status_code == 200
    assert _Inputs(removed.text).inputs["expires_at"]["value"] == ""
    await verify_db.rollback()
    assert (await verify_db.get(Gallery, gallery.id)).expires_at is None


@pytest.mark.parametrize("locale", ["de", "en"])
async def test_past_date_is_rejected_with_localized_message_without_changing_saved_expiry(
    client, db, owner, verify_db, monkeypatch, locale
):
    _freeze_now(monkeypatch, datetime(2030, 10, 30, 12, tzinfo=UTC))
    original = datetime(2030, 11, 1, 14, 30, tzinfo=UTC)
    gallery, _ = await _gallery(db, owner, original)
    csrf = _authenticate(client, owner, locale)
    rejected = await client.post(
        f"/galleries/{gallery.id}/expiry", data={"expires_at": "2030-10-29", "csrf_token": csrf}
    )
    assert rejected.status_code == 200
    assert rejected.headers["HX-Reswap"] == "none"
    message = json.loads(rejected.headers["HX-Trigger"])["showToast"]["message"]
    assert message == (
        "Wähle heute oder ein zukünftiges Datum (UTC)." if locale == "de" else "Choose today or a future date (UTC)."
    )
    assert (await verify_db.get(Gallery, gallery.id)).expires_at == original


@pytest.mark.parametrize("raw", ["2030-10-31T12:00", "2030-02-30", "20301031", "9999-12-31"])
async def test_noncanonical_date_form_values_are_rejected(client, db, owner, verify_db, raw):
    gallery, _ = await _gallery(db, owner)
    csrf = _authenticate(client, owner)
    response = await client.post(f"/galleries/{gallery.id}/expiry", data={"expires_at": raw, "csrf_token": csrf})
    assert response.status_code == 422
    assert (await verify_db.get(Gallery, gallery.id)).expires_at is None


async def test_expiry_form_preserves_ownership_check(client, db, owner, other_user):
    gallery, _ = await _gallery(db, owner)
    csrf = _authenticate(client, other_user)
    response = await client.post(
        f"/galleries/{gallery.id}/expiry", data={"expires_at": "2030-10-31", "csrf_token": csrf}
    )
    assert response.status_code == 404


async def test_json_api_preserves_precise_timestamp_contract(client, db, owner, auth_headers):
    instant = datetime(2031, 1, 2, 0, 30, 15, 123456, tzinfo=timezone(timedelta(hours=2)))
    response = await client.post(
        "/api/v1/galleries",
        json={"name": "Precise API expiry", "expires_at": instant.isoformat()},
        headers=auth_headers(owner),
    )
    assert response.status_code == 201
    assert datetime.fromisoformat(response.json()["expires_at"]) == instant
    gallery_id = response.json()["id"]
    updated = await client.patch(
        f"/api/v1/galleries/{gallery_id}",
        json={"expires_at": "2031-02-03T04:05:06.654321Z"},
        headers=auth_headers(owner),
    )
    assert updated.status_code == 200
    assert datetime.fromisoformat(updated.json()["expires_at"]) == datetime(2031, 2, 3, 4, 5, 6, 654321, tzinfo=UTC)
