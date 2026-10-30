"""Quota enforcement across all gallery allocation paths."""

import asyncio

import pytest
from sqlalchemy import func, select

import app.frontend.dashboard as dashboard_module
from app.auth.tokens import create_access_token
from app.config import settings
from app.db.models import Gallery, User
from app.galleries.quota import GalleryQuotaExceeded, reserve_gallery_slot
from app.security.middleware import _mint_csrf_token
from tests.integration.conftest import TestSession


async def test_duplicate_at_limit_and_unlimited_override(client, db, owner, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 1)
    source = Gallery(owner_id=owner.id, name="Source")
    db.add(source)
    await db.commit()
    url = f"/api/v1/galleries/{source.id}/duplicate"
    response = await client.post(url, json={}, headers=auth_headers(owner))
    assert response.status_code == 409
    assert await db.scalar(select(func.count()).select_from(Gallery)) == 1

    owner.gallery_limit_override = 0
    await db.commit()
    response = await client.post(url, json={}, headers=auth_headers(owner))
    assert response.status_code == 201
    assert await db.scalar(select(func.count()).select_from(Gallery)) == 2


async def test_reservation_serializes_and_rechecks_after_commit(db, owner, monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 1)
    async with TestSession() as first, TestSession() as second:
        await reserve_gallery_slot(owner.id, first)
        waiting = asyncio.create_task(reserve_gallery_slot(owner.id, second))
        await asyncio.sleep(0.1)
        assert not waiting.done()
        first.add(Gallery(owner_id=owner.id, name="First"))
        await first.commit()
        with pytest.raises(GalleryQuotaExceeded):
            await asyncio.wait_for(waiting, 5)


async def test_reservation_allows_foreign_key_checks(db, owner, monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 1)
    async with TestSession() as holder, TestSession() as other:
        await reserve_gallery_slot(owner.id, holder)
        other.add(Gallery(owner_id=owner.id, name="Foreign key check"))
        await asyncio.wait_for(other.flush(), 5)
        await other.rollback()
        await holder.rollback()


async def test_dashboard_uses_current_override_after_authentication(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 1)
    original_reserve = dashboard_module.reserve_gallery_slot

    async def update_then_reserve(owner_id, session):
        async with TestSession() as updater:
            current = await updater.get(User, owner_id)
            current.gallery_limit_override = 2
            await updater.commit()
        await original_reserve(owner_id, session)

    monkeypatch.setattr(dashboard_module, "reserve_gallery_slot", update_then_reserve)
    token = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    response = await client.post(
        "/dashboard/galleries", data={"name": "New", "csrf_token": csrf}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert "gallery-limit-notice" not in response.text


async def test_competing_allocation_routes_share_last_slot(client, db, owner, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "max_galleries_per_user", 2)
    source = Gallery(owner_id=owner.id, name="Source")
    db.add(source)
    await db.commit()
    token = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    api, duplicate, dashboard = await asyncio.gather(
        client.post("/api/v1/galleries", json={"name": "API"}, headers=auth_headers(owner)),
        client.post(f"/api/v1/galleries/{source.id}/duplicate", json={}, headers=auth_headers(owner)),
        client.post(
            "/dashboard/galleries", data={"name": "Dashboard", "csrf_token": csrf}, headers={"HX-Request": "true"}
        ),
    )
    successes = (
        (api.status_code == 201)
        + (duplicate.status_code == 201)
        + (dashboard.status_code == 200 and "HX-Refresh" not in dashboard.headers)
    )
    assert successes == 1
    assert await db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == owner.id)) == 2
