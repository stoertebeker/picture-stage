"""Cumulative storage admission against isolated PostgreSQL and real files."""

import asyncio
import io
import json
import uuid
from contextlib import AsyncExitStack

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Image, ImageProcessingStatus, StorageAllocation, StorageUsage, UserStatus
from app.images import preview_worker
from app.images.upload_batch import UploadBatch
from app.main import app
from app.storage.quota import release, reserve

from .conftest import TestSession, make_user, test_engine
from .test_gallery_storage_cleanup import _create_gallery, _delete_gallery, _files, _png_bytes
from .test_gallery_storage_cleanup import local_storage as local_storage
from .test_storage_cleanup_failures import worker as worker
from .test_upload_batch_cleanup import _post


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


async def _usage(owner_id):
    async with TestSession() as fresh:
        usage = await fresh.get(StorageUsage, owner_id)
        return (usage.bytes, usage.objects, usage.unknown_sizes) if usage else (0, 0, 0)


@pytest.mark.parametrize("entrypoint,objects", [("api", 4), ("frontend", 1)])
async def test_repeated_uploads_share_owner_object_quota(
    client, db, owner, auth_headers, local_storage, monkeypatch, entrypoint, objects
):
    async def no_background(*args):
        pass

    monkeypatch.setattr(preview_worker, "process_image_previews", no_background)
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", objects, raising=False)
    first = await _create_gallery(db, owner, "First")
    second = await _create_gallery(db, owner, "Second")
    files = [("files", ("photo.png", _png_bytes(), "image/png"))]
    accepted = await _post(client, owner, first, auth_headers, entrypoint, files)
    assert accepted.status_code == (201 if entrypoint == "api" else 200), accepted.text
    retained = _files(local_storage, first)
    rejected = await _post(client, owner, second, auth_headers, entrypoint, files)
    assert rejected.status_code == 409, rejected.text
    assert _files(local_storage, first) == retained
    assert _files(local_storage, second) == {}


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_original_byte_quota_rejects_before_first_write(
    client, db, owner, auth_headers, local_storage, monkeypatch, entrypoint
):
    monkeypatch.setattr(settings, "max_storage_bytes_per_owner", len(_png_bytes()) - 1, raising=False)
    gallery = await _create_gallery(db, owner, "Byte limit")
    files = [("files", ("photo.png", _png_bytes(), "image/png"))]
    response = await _post(client, owner, gallery, auth_headers, entrypoint, files)
    assert response.status_code == 409, response.text
    assert _files(local_storage, gallery) == {}


async def test_concurrent_galleries_cannot_spend_same_owner_allowance(
    client, db, owner, auth_headers, local_storage, monkeypatch
):
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 4, raising=False)
    galleries = [await _create_gallery(db, owner, name) for name in ("Parallel A", "Parallel B")]
    files = [("files", ("photo.png", _png_bytes(), "image/png"))]
    responses = await asyncio.gather(
        *(_post(client, owner, gallery, auth_headers, "api", files) for gallery in galleries)
    )
    statuses = [response.status_code for response in responses]
    assert set(statuses) <= {201, 409}
    assert statuses.count(201) <= 1
    assert 409 in statuses
    assert sum(len(_files(local_storage, gallery)) for gallery in galleries) <= 4


@pytest.mark.parametrize("locale,text", [("de", "Dein Speicherlimit"), ("en", "Your storage limit")])
async def test_html_quota_error_is_localized(client, db, owner, auth_headers, local_storage, monkeypatch, locale, text):
    monkeypatch.setattr(settings, "max_storage_bytes_per_owner", 1)
    gallery = await _create_gallery(db, owner, "Localized")
    client.cookies.set("lang", locale)
    response = await _post(
        client, owner, gallery, auth_headers, "frontend", [("files", ("a.png", _png_bytes(), "image/png"))]
    )
    assert response.status_code == 409
    assert text in response.json()["detail"]
    assert response.headers["X-Picture-Stage-Error"] == "storage-quota"
    assert json.loads(response.headers["HX-Trigger"])["showToast"] == {
        "kind": "danger",
        "message": response.json()["detail"],
    }


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_delete_refunds_quota_and_allows_replacement(
    client, db, owner, auth_headers, local_storage, monkeypatch, entrypoint
):
    async def no_background(*args):
        pass

    monkeypatch.setattr(preview_worker, "process_image_previews", no_background)
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 4 if entrypoint == "api" else 1)
    gallery = await _create_gallery(db, owner, "Refund")
    files = [("files", ("photo.png", _png_bytes(), "image/png"))]
    assert (await _post(client, owner, gallery, auth_headers, entrypoint, files)).is_success
    before = await _usage(owner.id)
    assert before[0] == sum(map(len, _files(local_storage, gallery).values()))
    assert before[1] == len(_files(local_storage, gallery))
    response = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert response.status_code in {204, 303}
    assert await _usage(owner.id) == (0, 0, 0)
    replacement = await _create_gallery(db, owner, "Replacement")
    assert (await _post(client, owner, replacement, auth_headers, entrypoint, files)).is_success


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_failed_write_remains_charged_until_confirmed_cleanup(
    client, db, owner, auth_headers, local_storage, monkeypatch, cleanup_fails
):
    gallery = await _create_gallery(db, owner, "Partial write")
    other = await _create_gallery(db, owner, "Blocked by partial write")
    actual_upload, actual_delete = local_storage.upload, local_storage.delete
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 1)

    async def partial(key, data, content_type):
        await actual_upload(key, io.BytesIO(b"partial"), content_type)
        raise OSError("uncertain storage write")

    async def fail_delete(key):
        raise OSError("cleanup offline")

    monkeypatch.setattr(local_storage, "upload", partial)
    if cleanup_fails:
        monkeypatch.setattr(local_storage, "delete", fail_delete)
    files = [("files", ("photo.png", _png_bytes(), "image/png"))]
    response = await _post(client, owner, gallery, auth_headers, "frontend", files)
    assert response.status_code == 500
    assert await _usage(owner.id) == ((len(_png_bytes()), 1, 0) if cleanup_fails else (0, 0, 0))
    if cleanup_fails:
        monkeypatch.setattr(local_storage, "upload", actual_upload)
        response = await _post(client, owner, other, auth_headers, "frontend", files)
        assert response.status_code == 409
        monkeypatch.setattr(local_storage, "delete", actual_delete)
        assert (await _delete_gallery(client, gallery, owner, auth_headers, "api")).status_code == 204
        assert await _usage(owner.id) == (0, 0, 0)


@pytest.mark.parametrize("objects,expected", [(3, ImageProcessingStatus.failed), (4, ImageProcessingStatus.ready)])
async def test_background_previews_reserve_before_each_write(
    client, db, owner, auth_headers, local_storage, monkeypatch, worker, objects, expected
):
    actual_worker = worker

    async def no_background(*args):
        pass

    monkeypatch.setattr(preview_worker, "process_image_previews", no_background)
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", objects)
    gallery = await _create_gallery(db, owner, "Worker quota")
    response = await _post(
        client, owner, gallery, auth_headers, "frontend", [("files", ("photo.png", _png_bytes(), "image/png"))]
    )
    assert response.status_code == 200
    image = await db.scalar(select(Image).where(Image.gallery_id == gallery.id))
    await actual_worker(image.id, gallery.id)
    async with TestSession() as fresh:
        assert await fresh.scalar(select(Image.processing_status).where(Image.id == image.id)) == expected
    files = _files(local_storage, gallery)
    assert len(files) == (1 if expected == ImageProcessingStatus.failed else 4)
    assert await _usage(owner.id) == (sum(map(len, files.values())), len(files), 0)


@pytest.mark.parametrize("ack_lost", [False, True])
async def test_commit_failure_refunds_only_uncommitted_objects(
    client, db, owner, auth_headers, local_storage, monkeypatch, ack_lost
):
    gallery = await _create_gallery(db, owner, "Commit outcome")
    real_enter = UploadBatch.__aenter__

    async def enter(batch):
        result = await real_enter(batch)
        real_commit = batch.db.commit

        async def fail_commit():
            if ack_lost:
                await real_commit()
            raise OSError("commit acknowledgement lost")

        monkeypatch.setattr(batch.db, "commit", fail_commit)
        return result

    monkeypatch.setattr(UploadBatch, "__aenter__", enter)
    response = await _post(
        client, owner, gallery, auth_headers, "api", [("files", ("photo.png", _png_bytes(), "image/png"))]
    )
    assert response.status_code == 500
    files = _files(local_storage, gallery)
    assert len(files) == (4 if ack_lost else 0)
    assert await _usage(owner.id) == (sum(map(len, files.values())), len(files), 0)


async def test_duplicates_use_same_owner_quota_and_compensate_partial_copy(
    client, db, owner, auth_headers, local_storage, monkeypatch
):
    source = await _create_gallery(db, owner, "Copy quota")
    response = await _post(
        client, owner, source, auth_headers, "api", [("files", ("photo.png", _png_bytes(), "image/png"))]
    )
    assert response.status_code == 201
    retained = _files(local_storage, source)
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 5)
    response = await client.post(f"/api/v1/galleries/{source.id}/duplicate", headers=auth_headers(owner), json={})
    assert response.status_code == 409, response.text
    assert _files(local_storage, source) == retained
    assert await _usage(owner.id) == (sum(map(len, retained.values())), 4, 0)
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 8)
    response = await client.post(f"/api/v1/galleries/{source.id}/duplicate", headers=auth_headers(owner), json={})
    assert response.status_code == 201, response.text
    assert (await _usage(owner.id))[1] == 8


async def test_user_delete_cleans_claim_with_uncommitted_gallery_id(client, db, owner, auth_headers, local_storage):
    admin = await make_user(db, "quota-admin@test.local", UserStatus.admin)
    gallery_id, image_id = uuid.uuid4(), uuid.uuid4()
    key = f"{gallery_id}/originals/{image_id}.png"
    await reserve(db, owner.id, gallery_id, image_id, key, 7)
    await local_storage.upload(key, io.BytesIO(b"partial"), "image/png")
    response = await client.delete(f"/api/v1/admin/users/{owner.id}", headers=auth_headers(admin))
    assert response.status_code == 204, response.text
    assert not await local_storage.exists(key)
    async with TestSession() as fresh:
        assert await fresh.get(StorageAllocation, key) is None
        assert await fresh.get(StorageUsage, owner.id) is None


@pytest.mark.parametrize("slots", [1, 3])
async def test_accounting_progresses_when_every_parent_pool_slot_is_held(owner, slots):
    """Independent accounting cannot wait for the connections its callers hold.

    Each parametrized invocation has its own pytest event loop, also guarding
    against caching accounting connections across loop lifetimes.
    """
    engine = create_async_engine(test_engine.url, pool_size=slots, max_overflow=0, pool_timeout=0.25)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    gallery_id, image_id = uuid.uuid4(), uuid.uuid4()
    keys = [f"{gallery_id}/originals/{index}.png" for index in range(slots)]
    try:
        async with AsyncExitStack() as stack:
            parents = [await stack.enter_async_context(sessions()) for _ in range(slots)]
            for parent in parents:
                await parent.execute(select(1))
            await asyncio.wait_for(
                asyncio.gather(
                    *(
                        reserve(parent, owner.id, gallery_id, image_id, key, 7)
                        for parent, key in zip(parents, keys, strict=True)
                    )
                ),
                timeout=10,
            )
            assert await _usage(owner.id) == (7 * slots, slots, 0)
            await asyncio.wait_for(
                asyncio.gather(*(release(parent, [key]) for parent, key in zip(parents, keys, strict=True))), timeout=10
            )
            assert await _usage(owner.id) == (0, 0, 0)
    finally:
        await engine.dispose()


async def test_large_release_supports_default_owner_object_allowance(db, owner):
    """A legitimate gallery can exceed asyncpg's 32,767 scalar-bind ceiling."""
    gallery_id, image_id = uuid.uuid4(), uuid.uuid4()
    prefix = f"{gallery_id}/previews/{image_id}_"
    db.add(StorageUsage(owner_id=owner.id, bytes=33_000, objects=33_000, unknown_sizes=0))
    await db.execute(
        text(
            "INSERT INTO storage_allocations (key, owner_id, gallery_id, image_id, size) "
            "SELECT :prefix || n::text, :owner, :gallery, :image, 1 FROM generate_series(1, 33000) n"
        ),
        {"prefix": prefix, "owner": owner.id, "gallery": gallery_id, "image": image_id},
    )
    await db.commit()
    keys = [f"{prefix}{index}" for index in range(1, 33_001)]
    await release(db, keys)
    assert await _usage(owner.id) == (0, 0, 0)
    # A retry must never refund the same objects twice.
    await release(db, keys)
    assert await _usage(owner.id) == (0, 0, 0)
