"""Gallery password routes share bounded hashing without changing their contracts."""

import json

import pytest
from sqlalchemy import select

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus
from app.galleries import password_hashing
from app.i18n import t
from app.security.middleware import _mint_csrf_token


async def _post(client, owner, gallery, auth_headers, route, password, *, htmx=False, locale="de"):
    if route == "api":
        return await client.post(
            f"/api/v1/galleries/{gallery.id}/share", json={"password": password}, headers=auth_headers(owner)
        )
    token = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    action = "share" if route == "frontend_share" else "password"
    headers = {"X-CSRF-Token": csrf, "Accept-Language": locale}
    if htmx:
        headers["HX-Request"] = "true"
    return await client.post(f"/galleries/{gallery.id}/{action}", data={"password": password or ""}, headers=headers)


@pytest.mark.parametrize("route", ["api", "frontend_share", "password"])
async def test_busy_hash_rejects_without_changing_gallery(
    client, db, owner, auth_headers, verify_db, monkeypatch, route
):
    old_hash = hash_password("existing")
    gallery = Gallery(
        owner_id=owner.id,
        name="Existing share",
        status=GalleryStatus.shared,
        share_token="existing-token",
        share_token_hash=b"existing-hash",
        share_token_salt=b"existing-salt",
        password_hash=old_hash,
    )
    db.add(gallery)
    await db.commit()
    monkeypatch.setattr(password_hashing, "_MAX_CONCURRENT_HASHES", 0)

    response = await _post(client, owner, gallery, auth_headers, route, "new-password")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"detail": "Gallery password processing is busy"}
    saved = await verify_db.scalar(select(Gallery).where(Gallery.id == gallery.id))
    assert saved.share_token == "existing-token"
    assert saved.password_hash == old_hash
    assert saved.status == GalleryStatus.shared


@pytest.mark.parametrize("route", ["frontend_share", "password"])
@pytest.mark.parametrize("locale", ["de", "en"])
async def test_busy_htmx_shows_localized_toast_without_swapping(
    client, db, owner, auth_headers, verify_db, monkeypatch, route, locale
):
    gallery = Gallery(owner_id=owner.id, name="Busy share", share_token="existing")
    db.add(gallery)
    await db.commit()
    monkeypatch.setattr(password_hashing, "_MAX_CONCURRENT_HASHES", 0)

    response = await _post(client, owner, gallery, auth_headers, route, "new-password", htmx=True, locale=locale)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["hx-reswap"] == "none"
    toast = json.loads(response.headers["hx-trigger"])["showToast"]
    assert toast == {"kind": "error", "message": t("gallery.password_processing_busy", locale)}
    assert toast["message"] != "gallery.password_processing_busy"
    saved = await verify_db.scalar(select(Gallery).where(Gallery.id == gallery.id))
    assert saved.share_token == "existing"
    assert saved.password_hash is None


@pytest.mark.parametrize("route", ["api", "frontend_share", "password"])
async def test_successful_hash_and_password_removal(client, db, owner, auth_headers, verify_db, route):
    gallery = Gallery(owner_id=owner.id, name="Share", status=GalleryStatus.draft)
    db.add(gallery)
    await db.commit()

    response = await _post(client, owner, gallery, auth_headers, route, "valid password")
    assert response.status_code == 200
    saved = await verify_db.scalar(select(Gallery).where(Gallery.id == gallery.id))
    assert verify_password("valid password", saved.password_hash)
    token = saved.share_token

    # Unprotected requests must not consume bcrypt capacity.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(password_hashing, "_MAX_CONCURRENT_HASHES", 0)
        response = await _post(client, owner, gallery, auth_headers, route, None)
    assert response.status_code == 200
    await verify_db.refresh(saved)
    assert saved.password_hash is None
    if route == "password":
        assert saved.share_token == token
