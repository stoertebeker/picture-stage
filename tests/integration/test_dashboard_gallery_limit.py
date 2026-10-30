"""Visible dashboard quota state, including HTMX updates and stale tabs (GitHub #2)."""

from html.parser import HTMLParser

import pytest
from sqlalchemy import func, select

from app.auth.tokens import create_access_token
from app.config import settings
from app.db.models import Gallery
from app.security.middleware import _mint_csrf_token


class _Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def _authenticate(client, owner, locale="de"):
    token = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    client.cookies.set("lang", locale)
    return csrf


def _assert_controls(html, limited, *, oob=False):
    elements = _Elements(html).elements
    controls = [attrs for _, attrs in elements if attrs.get("id") == "gallery-create-controls"]
    assert len(controls) == 1
    assert (controls[0].get("hx-swap-oob") == "outerHTML") == oob
    buttons = [attrs for tag, attrs in elements if tag == "button" and attrs.get("data-open-dialog") == "createModal"]
    assert buttons
    assert all(("disabled" in attrs) == limited for attrs in buttons)
    notices = [attrs for _, attrs in elements if attrs.get("id") == "gallery-limit-notice"]
    assert len(notices) == int(limited)
    if limited:
        assert notices[0]["role"] == "status"
        assert buttons[0]["aria-describedby"] == "gallery-limit-notice"


@pytest.mark.parametrize(
    ("global_limit", "override", "count", "limited"),
    [
        (2, None, 1, False),
        (2, None, 2, True),
        (5, 1, 1, True),
        (1, 3, 2, False),
        (1, 0, 2, False),
        (0, None, 2, False),
        (1, None, 2, True),
    ],
)
async def test_dashboard_shows_effective_limit(
    client, db, owner, other_user, monkeypatch, global_limit, override, count, limited
):
    monkeypatch.setattr(settings, "max_galleries_per_user", global_limit)
    owner.gallery_limit_override = override
    db.add_all([Gallery(owner_id=owner.id, name=f"Owned {i}") for i in range(count)])
    db.add(Gallery(owner_id=other_user.id, name="Someone else's gallery"))
    await db.commit()
    _authenticate(client, owner)
    response = await client.get("/dashboard")
    assert response.status_code == 200
    _assert_controls(response.text, limited)
    if limited:
        assert f"Galerie-Limit erreicht ({override if override is not None else global_limit})" in response.text
        assert "Lösche eine bestehende Galerie" in response.text


@pytest.mark.parametrize(
    ("locale", "message"), [("de", "Galerie-Limit erreicht (1)"), ("en", "Gallery limit reached (1)")]
)
async def test_last_create_updates_controls_and_delete_restores_creation(
    client, db, owner, monkeypatch, locale, message
):
    monkeypatch.setattr(settings, "max_galleries_per_user", 1)
    csrf = _authenticate(client, owner, locale)
    _assert_controls((await client.get("/dashboard")).text, False)
    response = await client.post(
        "/dashboard/galleries",
        data={"name": "Last allowed gallery", "csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    _assert_controls(response.text, True, oob=True)
    assert '<span id="dashboard-gallery-count" hx-swap-oob="outerHTML">1</span>' in response.text
    assert message in response.text
    assert "Last allowed gallery" in response.text
    gallery = await db.scalar(select(Gallery).where(Gallery.owner_id == owner.id))
    deleted = await client.post(f"/galleries/{gallery.id}/delete", data={"confirm_delete": "yes", "csrf_token": csrf})
    assert deleted.status_code == 303
    _assert_controls((await client.get(deleted.headers["location"])).text, False)


@pytest.mark.parametrize("htmx", [True, False])
async def test_stale_create_refreshes_persistent_notice_without_success(client, db, owner, monkeypatch, htmx):
    monkeypatch.setattr(settings, "max_galleries_per_user", 1)
    csrf = _authenticate(client, owner)
    _assert_controls((await client.get("/dashboard")).text, False)
    # Another tab consumes the final slot while this tab's form remains open.
    db.add(Gallery(owner_id=owner.id, name="Created in another tab"))
    await db.commit()
    response = await client.post(
        "/dashboard/galleries",
        data={"name": "Must not be created", "csrf_token": csrf},
        headers={"HX-Request": "true"} if htmx else {},
    )
    assert "HX-Trigger" not in response.headers
    if htmx:
        assert response.status_code == 200
        assert response.headers["HX-Refresh"] == "true"
        assert not response.text
    else:
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"
    assert await db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == owner.id)) == 1
    refreshed = await client.get("/dashboard")
    _assert_controls(refreshed.text, True)
    assert "Created in another tab" in refreshed.text
    assert "Must not be created" not in refreshed.text
