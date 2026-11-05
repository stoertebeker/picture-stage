"""Real HTML unlock → HttpOnly cookie → grid / selection / completion boundary."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.db.models import Gallery, GalleryStatus, Image, SelectionEvent, ShareSession
from app.db.session import get_db
from app.guest import credentials as creds
from app.guest import router as guest_router
from app.security.rate_limit import limiter
from tests.integration.conftest import _override_get_db
from tests.integration.guest_helpers import guest_csrf, password_form, response_session_id
from tests.integration.test_guest_session_revocation import _gallery


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    monkeypatch.setattr(guest_router, "send_notification", AsyncMock())
    monkeypatch.setattr(guest_router, "notify_owner_gallery_completed", AsyncMock())


@pytest_asyncio.fixture
async def api_client():
    api = FastAPI()
    api.state.limiter = limiter
    api.include_router(guest_router.router)
    api.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        yield client


async def test_browser_unlock_never_exposes_session_in_dom_urls_or_json(client, db, owner, verify_db):
    gallery, image, token = await _gallery(db, owner)
    # Include both the initial grid and the infinite-scroll URL producer.
    db.add_all([Image(gallery_id=gallery.id, filename=f"proof-{n}.jpg", storage_key=f"proof-{n}") for n in range(31)])
    await db.commit()
    owner_cookie, owner_csrf = "owner-session-unchanged", "owner-csrf-unchanged"
    client.cookies.set("session", owner_cookie)
    client.cookies.set("csrf_token", owner_csrf)
    unlocked = await password_form(client, f"/g/{token}", "old-password")
    assert unlocked.status_code == 200, unlocked.text
    session_id = response_session_id(unlocked)
    header = next(value for value in unlocked.headers.get_list("set-cookie") if value.startswith("guest_session_"))
    assert "HttpOnly" in header and "SameSite=lax" in header and "Domain=" not in header
    assert f"Path=/g/{token}" in header
    assert 'hx-trigger="revealed"' in unlocked.text
    for forbidden in (session_id, "session_id", "data-session-id"):
        assert forbidden not in unlocked.text
    headers = {creds.CSRF_HEADER: guest_csrf(unlocked)}
    for surface in ("gallery-images", "images", "selections"):
        result = await client.get(f"/g/{token}/{surface}")
        assert result.status_code == 200, result.text
        assert session_id not in result.text
        assert "private" in result.headers["cache-control"] and "no-store" in result.headers["cache-control"]
    selection = await client.post(
        f"/g/{token}/selections", json={"image_id": str(image.id), "action": "select"}, headers=headers
    )
    assert selection.status_code == 201, selection.text
    assert session_id not in selection.text
    assert await verify_db.scalar(select(SelectionEvent.share_session_id)) == uuid.UUID(session_id)
    completed = await client.post(f"/g/{token}/complete", json={}, headers=headers)
    assert completed.status_code == 200, completed.text
    assert (await client.post(f"/g/{token}/complete", json={}, headers=headers)).status_code == 409
    assert client.cookies.get("session") == owner_cookie and client.cookies.get("csrf_token") == owner_csrf


@pytest.mark.parametrize("surface", ["gallery-images", "images", "selections"])
async def test_query_uuid_is_not_a_read_credential_but_explicit_header_still_is(client, api_client, db, owner, surface):
    _gallery_row, _image, token = await _gallery(db, owner)
    verified = await api_client.post(f"/g/{token}/verify-password", json={"password": "old-password"})
    assert verified.status_code == 200
    session_id = verified.json()["session_id"]
    assert not verified.cookies
    path = f"/g/{token}/{surface}"
    for params in ({}, {"session_id": session_id}, {"session_id": ""}, {"session_id": "null"}):
        denied = await client.get(path, params=params)
        assert denied.status_code == 403
        assert "private-proof.jpg" not in denied.text and "/media/" not in denied.text
    allowed = await client.get(path, headers={creds.SESSION_HEADER: session_id})
    assert allowed.status_code == 200
    assert "private" in allowed.headers["cache-control"]
    assert await db.get(ShareSession, uuid.UUID(session_id)) is not None


@pytest.mark.parametrize("mutation", ["selections", "complete"])
@pytest.mark.parametrize(
    "variant",
    ["missing", "wrong", "owner-only", "null", "body-empty", "body-invalid", "header-empty", "header-body-conflict"],
)
async def test_cookie_mutations_fail_closed_without_guest_proof(client, db, owner, verify_db, mutation, variant):
    gallery, image, token = await _gallery(db, owner)
    unlocked = await password_form(client, f"/g/{token}", "old-password")
    valid_id = response_session_id(unlocked)
    body = {"image_id": str(image.id), "action": "select"} if mutation == "selections" else {}
    headers = {}
    if variant == "wrong":
        headers[creds.CSRF_HEADER] = "wrong"
    elif variant == "owner-only":
        headers["X-CSRF-Token"] = guest_csrf(unlocked)
        headers["Authorization"] = "Bearer owner-not-guest"
    elif variant == "null":
        body["session_id"] = None
        headers[creds.CSRF_HEADER] = guest_csrf(unlocked)
    elif variant in {"body-empty", "body-invalid"}:
        body["session_id"] = "" if variant == "body-empty" else "not-a-uuid"
        headers[creds.CSRF_HEADER] = guest_csrf(unlocked)
    elif variant == "header-empty":
        headers[creds.SESSION_HEADER] = ""
        headers[creds.CSRF_HEADER] = guest_csrf(unlocked)
    elif variant == "header-body-conflict":
        headers[creds.SESSION_HEADER] = str(uuid.uuid4())
        body["session_id"] = valid_id
    rejected = await client.post(f"/g/{token}/{mutation}", json=body, headers=headers)
    assert rejected.status_code == (422 if variant in {"body-empty", "body-invalid"} else 403), rejected.text
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 0
    assert await verify_db.scalar(select(Gallery.status).where(Gallery.id == gallery.id)) == GalleryStatus.shared


async def test_parallel_galleries_have_distinct_cookies_and_csrf(client, db, owner):
    first, first_image, first_token = await _gallery(db, owner, token="first-gallery")
    second, second_image, second_token = await _gallery(db, owner, token="second-gallery")
    one = await password_form(client, f"/g/{first_token}", "old-password")
    two = await password_form(client, f"/g/{second_token}", "old-password")
    assert creds.cookie_name(first.id) != creds.cookie_name(second.id)
    assert client.cookies.get(creds.cookie_name(first.id)) and client.cookies.get(creds.cookie_name(second.id))
    for _gallery_row, image, token, own, other in (
        (first, first_image, first_token, one, two),
        (second, second_image, second_token, two, one),
    ):
        path = f"/g/{token}"
        body = {"image_id": str(image.id), "action": "select"}
        assert (await client.get(f"{path}/images")).status_code == 200
        denied = await client.post(f"{path}/selections", json=body, headers={creds.CSRF_HEADER: guest_csrf(other)})
        assert denied.status_code == 403
        accepted = await client.post(f"{path}/selections", json=body, headers={creds.CSRF_HEADER: guest_csrf(own)})
        assert accepted.status_code == 201


@pytest.mark.parametrize("state", ["expired", "tampered", "foreign", "old-epoch"])
async def test_cookie_is_expiring_gallery_bound_and_epoch_bound(client, db, owner, monkeypatch, state):
    gallery, image, token = await _gallery(db, owner)
    unlocked = await password_form(client, f"/g/{token}", "old-password")
    cookie = client.cookies.get(creds.cookie_name(gallery.id))
    if state == "old-epoch":
        await db.execute(update(Gallery).where(Gallery.id == gallery.id).values(authorization_epoch=1))
        await db.commit()
    elif state == "expired":
        deadline = int(cookie.split(".")[1])
        monkeypatch.setattr(creds.time, "time", lambda: deadline + 1)
    else:
        client.cookies.clear()
        if state == "foreign":
            cookie, _csrf = creds.browser_session(uuid.uuid4(), uuid.UUID(response_session_id(unlocked)))
        else:
            cookie += "x"
        client.cookies.set(creds.cookie_name(gallery.id), cookie)
    for surface in ("gallery-images", "images", "selections"):
        denied = await client.get(f"/g/{token}/{surface}")
        assert denied.status_code == 403 and "private-proof.jpg" not in denied.text
    gate = await client.get(f"/g/{token}")
    assert gate.status_code == 200 and "data-lookup-password" in gate.text
    assert "private-proof.jpg" not in gate.text
    selected = await client.post(
        f"/g/{token}/selections",
        json={"image_id": str(image.id), "action": "select"},
        headers={creds.CSRF_HEADER: guest_csrf(unlocked)},
    )
    assert selected.status_code == 403
    completed = await client.post(f"/g/{token}/complete", json={}, headers={creds.CSRF_HEADER: guest_csrf(unlocked)})
    assert completed.status_code == 404


async def test_password_success_canonical_get_reuses_fenced_grant_without_new_admission(client, db, owner):
    gallery, _image, token = await _gallery(db, owner)
    unlocked = await password_form(client, f"/g/{token}", "old-password")
    cookie = client.cookies.get(creds.cookie_name(gallery.id))
    csrf = guest_csrf(unlocked)
    with patch("app.frontend.guest.create_share_session", side_effect=AssertionError("unexpected admission")):
        viewer = await client.get(f"/g/{token}")
    assert viewer.status_code == 200
    assert "private-proof.jpg" in viewer.text
    assert "data-lookup-password" not in viewer.text
    assert client.cookies.get(creds.cookie_name(gallery.id)) == cookie
    assert guest_csrf(viewer) == csrf
    assert await db.scalar(select(func.count(ShareSession.id))) == 1


async def test_completed_grant_renders_completed_view_and_reopened_gallery_requires_new_visit(client, db, owner):
    gallery, _image, token = await _gallery(db, owner)
    unlocked = await password_form(client, f"/g/{token}", "old-password")
    session_id = uuid.UUID(response_session_id(unlocked))
    await db.execute(update(ShareSession).where(ShareSession.id == session_id).values(completed_at=datetime.now(UTC)))
    await db.execute(update(Gallery).where(Gallery.id == gallery.id).values(status=GalleryStatus.completed))
    await db.commit()
    assert "data-lookup-password" not in (await client.get(f"/g/{token}")).text
    await db.execute(update(Gallery).where(Gallery.id == gallery.id).values(status=GalleryStatus.shared))
    await db.commit()
    gate = await client.get(f"/g/{token}")
    assert gate.status_code == 200 and "data-lookup-password" in gate.text
    assert await db.scalar(select(func.count(ShareSession.id))) == 1


async def test_password_post_requires_gallery_bound_challenge_before_session_switch(client, db, owner):
    gallery, _image, token = await _gallery(db, owner)
    _other, _other_image, other_token = await _gallery(db, owner, token="other-gallery")
    path = f"/g/{token}"
    gate = await client.get(path)
    other_gate = await client.get(f"/g/{other_token}")
    for csrf in (None, "", "wrong", guest_csrf(other_gate)):
        data = {"password": "old-password"}
        if csrf is not None:
            data["guest_csrf_token"] = csrf
        denied = await client.post(f"{path}/verify-password", data=data)
        assert denied.status_code == 403
    assert await db.scalar(select(func.count(ShareSession.id))) == 0
    unlocked = await client.post(
        f"{path}/verify-password", data={"password": "old-password", "guest_csrf_token": guest_csrf(gate)}
    )
    assert unlocked.status_code == 200
    assert client.cookies.get(creds.cookie_name(gallery.id))


async def test_explicit_body_and_header_api_mutations_remain_supported(api_client, db, owner):
    _gallery_row, image, token = await _gallery(db, owner, protected=False)
    visit = await api_client.get(f"/g/{token}")
    assert visit.status_code == 200
    session_id = visit.json()["session_id"]
    selected = await api_client.post(
        f"/g/{token}/selections", json={"session_id": session_id, "image_id": str(image.id), "action": "select"}
    )
    assert selected.status_code == 201
    completed = await api_client.post(f"/g/{token}/complete", json={}, headers={creds.SESSION_HEADER: session_id})
    assert completed.status_code == 200


@pytest.mark.parametrize("protected", [False, True])
async def test_two_tabs_of_one_gallery_can_both_save_after_a_second_visit(client, db, owner, verify_db, protected):
    gallery, image, token = await _gallery(db, owner, protected=protected)
    path = f"/g/{token}"
    if protected:
        # Both forms can already be open before either tab unlocks. The second
        # unlock must preserve the first tab's cookie-bound mutation proof.
        gate_one = await client.get(path)
        gate_two = await client.get(path)
        tabs = []
        for gate in (gate_one, gate_two):
            tab = await client.post(
                f"{path}/verify-password",
                data={"password": "old-password", "guest_csrf_token": guest_csrf(gate)},
            )
            assert tab.status_code == 200, tab.text
            tabs.append(tab)
        # An already valid cookie must not turn a wrong password into success
        # or invalidate either of the successfully opened tabs.
        rejected = await client.post(
            f"{path}/verify-password",
            data={"password": "wrong-password", "guest_csrf_token": guest_csrf(gate_one)},
        )
        assert rejected.status_code == 401
    else:
        tabs = [await client.get(path), await client.get(path)]

    assert guest_csrf(tabs[0]) == guest_csrf(tabs[1])
    assert response_session_id(tabs[0]) == response_session_id(tabs[1])
    assert await verify_db.scalar(select(func.count(ShareSession.id)).where(ShareSession.gallery_id == gallery.id)) == 1
    for tab, action in zip(tabs, ("select", "favorite"), strict=True):
        saved = await client.post(
            f"{path}/selections",
            json={"image_id": str(image.id), "action": action},
            headers={creds.CSRF_HEADER: guest_csrf(tab)},
        )
        assert saved.status_code == 201, saved.text
    assert await verify_db.scalar(select(func.count(SelectionEvent.id))) == 2
