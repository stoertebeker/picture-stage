"""Browser language stays consistent through guest gallery requests (GitHub #5)."""

import uuid
from datetime import UTC, datetime, timedelta
from html import unescape
from unittest.mock import AsyncMock

import pytest

from app.auth.passwords import hash_password, hash_token
from app.db.models import Gallery, GalleryStatus, Image
from app.guest.credentials import cookie_name
from app.i18n import t
from tests.integration.guest_helpers import guest_csrf, password_form


async def _gallery(db, owner, *, protected=False):
    token = uuid.uuid4().hex
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Language proofing",
        status=GalleryStatus.shared,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        password_hash=hash_password("gallery-password") if protected else None,
    )
    db.add(gallery)
    await db.flush()
    image = Image(
        gallery_id=gallery.id,
        filename="portrait.png",
        storage_key=f"{gallery.id}/portrait.png",
        content_type="image/png",
        sort_order=1,
    )
    db.add(image)
    await db.commit()
    return gallery, image, f"/g/{token}"


def _assert_language(response, locale, *, status=200):
    assert response.status_code == status
    assert f'<html lang="{locale}"' in response.text
    assert "lang" not in response.cookies, "Automatic detection must not become a persistent explicit choice"


@pytest.mark.parametrize(
    ("language", "locale"),
    [("de-DE", "de"), ("en-US", "en"), ("fr-FR", "en"), ("de;q=0.2,en;q=0.9", "en")],
)
async def test_guest_browser_language_survives_grid_selection_and_completion(
    client, db, owner, monkeypatch, language, locale
):
    monkeypatch.setattr("app.guest.router.send_notification", AsyncMock())
    monkeypatch.setattr("app.guest.router.notify_owner_gallery_completed", AsyncMock())
    _gallery_record, image, path = await _gallery(db, owner)
    client.headers["Accept-Language"] = language

    viewer = await client.get(path)
    _assert_language(viewer, locale)
    assert t("guest.complete_title", locale) in unescape(viewer.text)
    assert 'href="/set-lang/de"' in viewer.text
    assert 'href="/set-lang/en"' in viewer.text
    headers = {"X-Guest-CSRF-Token": guest_csrf(viewer)}

    selection = await client.post(
        f"{path}/selections", json={"image_id": str(image.id), "action": "select"}, headers=headers
    )
    assert selection.status_code == 201
    grid = await client.get(f"{path}/gallery-images", params={"filter": "selected"})
    assert grid.status_code == 200
    assert t("guest.open_image", locale) in grid.text
    assert "portrait.png" in grid.text

    completed = await client.post(f"{path}/complete", json={}, headers=headers)
    assert completed.status_code == 200
    final = await client.get(path)
    _assert_language(final, locale)
    assert t("guest.completed_readonly", locale) in unescape(final.text)
    assert 'data-session-completed="true"' in final.text


@pytest.mark.parametrize(("language", "locale"), [("de-DE", "de"), ("fr-FR", "en")])
async def test_password_gate_and_verification_keep_browser_language(client, db, owner, language, locale):
    _gallery_record, _image, path = await _gallery(db, owner, protected=True)
    client.headers["Accept-Language"] = language
    gate = await client.get(path)
    _assert_language(gate, locale)
    assert t("guest.password_text", locale) in gate.text

    rejected = await password_form(client, path, "wrong-password")
    _assert_language(rejected, locale, status=401)
    assert t("guest.password_error", locale) in unescape(rejected.text)
    unlocked = await password_form(client, path, "gallery-password")
    _assert_language(unlocked, locale)
    assert t("guest.complete_title", locale) in unescape(unlocked.text)


async def test_manual_language_switch_overrides_browser_on_following_requests(client, db, owner):
    _gallery_record, _image, path = await _gallery(db, owner)
    client.headers["Accept-Language"] = "en-US"
    _assert_language(await client.get(path), "en")
    for locale in ("de", "en"):
        switched = await client.get(f"/set-lang/{locale}", headers={"Referer": f"http://test{path}"})
        assert switched.status_code == 303
        assert switched.headers["location"] == f"http://test{path}"
        assert switched.cookies["lang"] == locale
        client.headers["Accept-Language"] = "en-US" if locale == "de" else "de-DE"
        _assert_language(await client.get(path), locale)
        _assert_language(await client.get(path), locale)


@pytest.mark.parametrize(("language", "chosen"), [("en-US", "de"), ("de-DE", "en")])
async def test_switch_after_password_post_returns_to_gallery_get(client, db, owner, language, chosen):
    _gallery_record, _image, path = await _gallery(db, owner, protected=True)
    client.headers["Accept-Language"] = language
    unlocked = await password_form(client, path, "gallery-password")
    assert unlocked.status_code == 200
    assert 'x-data="langSwitcher"' in unlocked.text
    grant = client.cookies.get(cookie_name(_gallery_record.id))
    assert grant is not None
    switched = await client.get(f"/set-lang/{chosen}", headers={"Referer": f"http://test{path}/verify-password"})
    assert switched.status_code == 303
    assert switched.headers["location"] == path
    assert switched.cookies["lang"] == chosen
    # The canonical GET keeps the verified grant and renders the chosen locale.
    viewer = await client.get(switched.headers["location"])
    _assert_language(viewer, chosen)
    assert t("guest.complete_title", chosen) in unescape(viewer.text)
    assert "data-lookup-password" not in viewer.text
    assert client.cookies.get(cookie_name(_gallery_record.id)) == grant
    assert guest_csrf(viewer) == guest_csrf(unlocked)
    client.cookies.delete(cookie_name(_gallery_record.id))
    gate = await client.get(path)
    _assert_language(gate, chosen)
    assert t("guest.password_text", chosen) in gate.text


async def test_international_expired_gallery_and_non_guest_fallbacks(client, db, owner):
    gallery, _image, path = await _gallery(db, owner)
    gallery.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()
    client.headers["Accept-Language"] = "fr-FR"
    expired = await client.get(path)
    _assert_language(expired, "en", status=410)
    assert t("guest.expired_title", "en") in expired.text
    _assert_language(await client.get("/login"), "de")
