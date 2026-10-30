"""Indexed token lookup, legacy links and rolling-version compatibility."""

import secrets
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.passwords import hash_token, verify_token
from app.auth.token_lookup import legacy_token_scan, resolve_pending_signup, token_locator
from app.auth.tokens import create_access_token, generate_verification_token, verification_lookup_id
from app.db.models import Gallery, GalleryStatus, PendingSignup, UserStatus
from app.guest import service as guest_service
from app.security.middleware import _mint_csrf_token


def _pending(email: str, token: str, *, indexed: bool) -> PendingSignup:
    digest, salt = hash_token(token)
    return PendingSignup(
        email=email,
        password_hash="unused",
        verification_token_hash=digest,
        verification_token_salt=salt,
        verification_lookup_id=verification_lookup_id(token) if indexed else None,
    )


async def test_new_verification_lookup_old_reader_and_legacy_collision_fallback(db):
    new = generate_verification_token()
    legacy = secrets.token_urlsafe(32)
    # A legacy link imported from elsewhere can even resemble the new format.
    shaped_legacy = generate_verification_token()
    rows = [_pending("new@example.com", new, indexed=True), _pending("old@example.com", legacy, indexed=False)]
    rows.append(_pending("shaped@example.com", shaped_legacy, indexed=False))
    db.add_all(rows)
    await db.commit()
    with patch("app.auth.token_lookup.verify_token", wraps=verify_token) as checks:
        assert (await resolve_pending_signup(new, db)).email == "new@example.com"
        assert checks.call_count == 1
    assert verify_token(new, rows[0].verification_token_hash, rows[0].verification_token_salt)  # old app reader
    assert (await resolve_pending_signup(legacy, db)).email == "old@example.com"
    assert (await resolve_pending_signup(shaped_legacy, db)).email == "shaped@example.com"
    assert await resolve_pending_signup("invalid-token", db) is None


@pytest.mark.parametrize("route", ["api", "html"])
@pytest.mark.parametrize("indexed", [True, False])
async def test_both_verification_routes_accept_new_and_old_once(client, db, route, indexed):
    token = generate_verification_token() if indexed else secrets.token_urlsafe(32)
    pending = _pending("verify@example.com", token, indexed=indexed)
    db.add(pending)
    await db.commit()
    path = f"/api/v1/auth/verify-email/{token}" if route == "api" else f"/verify-email/{token}"
    first = await (client.post(path) if route == "api" else client.get(path))
    second = await (client.post(path) if route == "api" else client.get(path))
    assert first.status_code == 200
    assert second.status_code == 404


@pytest.mark.parametrize("route", ["api", "html"])
async def test_new_signup_stores_locator_and_old_binary_can_verify(client, db, monkeypatch, route):
    sender = AsyncMock()
    if route == "api":
        monkeypatch.setattr("app.auth.router.send_verification_email", sender)
        response = await client.post(
            "/api/v1/auth/signup", json={"email": "new@example.com", "password": "correct-password"}
        )
        assert response.status_code == 201
    else:
        monkeypatch.setattr("app.frontend.auth.send_verification_email", sender)
        page = await client.get("/signup")
        response = await client.post(
            "/signup",
            data={
                "email": "new@example.com",
                "password": "correct-password",
                "password_confirm": "correct-password",
                "csrf_token": page.cookies["csrf_token"],
            },
        )
        assert response.status_code == 200
    pending = await db.scalar(select(PendingSignup).where(PendingSignup.email == "new@example.com"))
    token = sender.await_args.args[1]
    assert pending.verification_lookup_id == verification_lookup_id(token)
    assert verify_token(token, pending.verification_token_hash, pending.verification_token_salt)
    assert (await client.get(f"/verify-email/{token}")).status_code == 200


async def test_gallery_index_and_hash_only_or_stale_locator_links_remain_usable(client, db, owner):
    direct, legacy, stale = (secrets.token_urlsafe(32) for _ in range(3))
    rows = []
    for index, token in enumerate((direct, legacy, stale)):
        digest, salt = hash_token(token)
        rows.append(
            Gallery(
                owner_id=owner.id,
                name=f"gallery-{index}",
                status=GalleryStatus.shared,
                share_token=token if index != 1 else None,
                share_token_hash=digest,
                share_token_salt=salt,
                # A legacy app can change the token without updating the locator.
                share_token_lookup=token_locator(direct if index == 0 else "stale") if index != 1 else None,
            )
        )
    db.add_all(rows)
    await db.commit()
    with patch.object(guest_service, "verify_token", wraps=verify_token) as checks:
        assert (await guest_service.resolve_gallery_by_token(direct, db)).id == rows[0].id
        assert checks.call_count == 1
    assert (await guest_service.resolve_gallery_by_token(legacy, db)).id == rows[1].id
    assert (await guest_service.resolve_gallery_by_token(stale, db)).id == rows[2].id
    assert await guest_service.resolve_gallery_by_token("missing-token", db) is None
    for token in (direct, legacy, stale):
        assert (await client.get(f"/g/{token}")).status_code == 200
        assert (await client.get(f"/g/{token}/images")).status_code == 200
    owner.status = UserStatus.disabled
    await db.commit()
    assert await guest_service.resolve_gallery_by_token(direct, db) is None
    assert await guest_service.resolve_gallery_by_token(legacy, db) is None


@pytest.mark.parametrize("route", ["api", "html"])
async def test_share_writers_set_and_revoke_locator(client, db, owner, auth_headers, route):
    gallery = Gallery(owner_id=owner.id, name="new share")
    db.add(gallery)
    await db.commit()
    if route == "api":
        path = f"/api/v1/galleries/{gallery.id}/share"
        headers = auth_headers(owner)
        response = await client.post(path, json={}, headers=headers)
    else:
        path = f"/galleries/{gallery.id}/share"
        access = create_access_token(str(owner.id))
        csrf = _mint_csrf_token(access)
        client.cookies.set("session", access)
        client.cookies.set("csrf_token", csrf)
        headers = {"X-CSRF-Token": csrf, "HX-Request": "true"}
        response = await client.post(path, data={}, headers=headers)
    assert response.status_code == 200
    await db.refresh(gallery)
    assert gallery.share_token_lookup == token_locator(gallery.share_token)
    assert verify_token(gallery.share_token, gallery.share_token_hash, gallery.share_token_salt)
    assert (await client.delete(path, headers=headers)).status_code in (200, 204)
    await db.refresh(gallery)
    assert gallery.share_token is gallery.share_token_hash is gallery.share_token_lookup is None


async def test_legacy_paths_have_separate_bounded_capacity_and_release(db, owner, monkeypatch):
    monkeypatch.setattr("app.auth.token_lookup._MAX_LEGACY_SCANS", 1)
    token = secrets.token_urlsafe(32)
    digest, salt = hash_token(token)
    db.add(
        Gallery(
            owner_id=owner.id,
            name="legacy",
            status=GalleryStatus.shared,
            share_token_hash=digest,
            share_token_salt=salt,
        )
    )
    db.add(_pending("legacy@example.com", token, indexed=False))
    await db.commit()
    async with legacy_token_scan("gallery"):
        with pytest.raises(HTTPException) as exc:
            await guest_service.resolve_gallery_by_token(token, db)
        assert exc.value.status_code == 503
        assert exc.value.headers == {"Retry-After": "1"}
        assert await resolve_pending_signup(token, db) is not None
    async with legacy_token_scan("verification"):
        with pytest.raises(HTTPException) as exc:
            await resolve_pending_signup(token, db)
        assert exc.value.status_code == 503
        assert await guest_service.resolve_gallery_by_token(token, db) is not None
    assert await guest_service.resolve_gallery_by_token(token, db) is not None
    assert await resolve_pending_signup(token, db) is not None
