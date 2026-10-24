"""Storage failures and producer/deletion interleavings on real PostgreSQL/files."""

import asyncio
import io
import uuid

import pytest
from sqlalchemy import func, select

from app.auth.tokens import create_access_token
from app.db.models import Gallery, Image, ImagePreview, ImageProcessingStatus
from app.images import preview_worker
from app.security.middleware import _mint_csrf_token
from app.storage.base import storage_key

from .conftest import TestSession
from .test_gallery_storage_cleanup import (
    _create_gallery,
    _delete_gallery,
    _files,
    _png_bytes,
    _upload_image,
)
from .test_gallery_storage_cleanup import (
    local_storage as local_storage,
)


async def _pending_image(db, owner, storage, gallery=None):
    gallery = gallery or await _create_gallery(db, owner, "Pending previews")
    image_id = uuid.uuid4()
    key = storage_key(str(gallery.id), "originals", f"{image_id}.png")
    await storage.upload(key, io.BytesIO(_png_bytes()), "image/png")
    image = Image(
        id=image_id, gallery_id=gallery.id, storage_key=key, filename="original.png", content_type="image/png"
    )
    db.add(image)
    await db.commit()
    return gallery, image


@pytest.fixture
def worker(monkeypatch, local_storage):
    async def render(fn, *args):
        return fn(*args)

    monkeypatch.setattr(preview_worker, "get_storage", lambda: local_storage)
    monkeypatch.setattr(preview_worker, "async_session", TestSession)
    monkeypatch.setattr(preview_worker, "run_in_pool", render)
    return preview_worker.process_image_previews


async def _image_status(image_id):
    async with TestSession() as fresh:
        return await fresh.scalar(select(Image.processing_status).where(Image.id == image_id))


@pytest.mark.parametrize(
    ("locale", "message"),
    [
        ("de", "Das Löschen konnte nicht abgeschlossen werden. Bitte versuche es erneut."),
        ("en", "Deletion could not be completed. Please try again."),
    ],
)
@pytest.mark.parametrize("entrypoint", ["frontend", "api"])
@pytest.mark.parametrize("kind", ["originals", "previews"])
async def test_failed_gallery_cleanup_retains_rows_and_can_retry(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, entrypoint, kind, locale, message
):
    client.cookies.set("lang", locale)
    gallery = await _create_gallery(db, owner, "Retry deletion")
    await _upload_image(client, gallery, owner, auth_headers)
    key = next(key for key in _files(local_storage, gallery) if f"/{kind}/" in key)
    actual_delete = local_storage.delete

    async def fail_one(candidate):
        if candidate == key:
            raise OSError("Injected storage-delete failure")
        await actual_delete(candidate)

    monkeypatch.setattr(local_storage, "delete", fail_one)
    response = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert response.status_code == 503
    assert key in _files(local_storage, gallery)
    assert await verify_db.get(Gallery, gallery.id) is not None
    assert await verify_db.scalar(select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id)) == 1
    assert await verify_db.scalar(select(func.count()).select_from(ImagePreview)) == 3
    if entrypoint == "frontend":
        assert message in response.text
        assert "gallery.delete_failed_" not in response.text
        assert 'role="alert"' in response.text
        assert 'href="/dashboard"' in response.text
    else:
        assert response.json()["detail"] == message

    monkeypatch.setattr(local_storage, "delete", actual_delete)
    response = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert response.status_code == (204 if entrypoint == "api" else 303)
    assert _files(local_storage, gallery) == {}
    assert (await verify_db.execute(select(Gallery).where(Gallery.id == gallery.id))).scalar_one_or_none() is None


async def test_failed_htmx_cleanup_shows_error_without_swap(
    client, db, owner, auth_headers, local_storage, monkeypatch
):
    gallery, image = await _pending_image(db, owner, local_storage)
    session = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(session)
    client.cookies.set("session", session)
    client.cookies.set("csrf_token", csrf)

    async def fail_delete(key):
        raise OSError("storage unavailable")

    monkeypatch.setattr(local_storage, "delete", fail_delete)
    response = await client.post(
        f"/galleries/{gallery.id}/bulk-delete",
        data={"image_ids": str(image.id), "csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 503
    assert response.headers["HX-Reswap"] == "none"
    assert '"kind": "error"' in response.headers["HX-Trigger"]
    assert await _image_status(image.id) == ImageProcessingStatus.pending


@pytest.mark.parametrize("fail_at", [1, 2, 3])
async def test_partial_upload_failure_compensates_even_the_raising_write(
    client, db, owner, auth_headers, local_storage, monkeypatch, worker, fail_at
):
    gallery, image = await _pending_image(db, owner, local_storage)
    actual_upload = local_storage.upload
    uploads = 0

    async def fail_after_partial_write(key, data, content_type):
        nonlocal uploads
        uploads += 1
        if uploads == fail_at:
            await actual_upload(key, io.BytesIO(b"partial object"), content_type)
            raise OSError("Injected upload failure after writing bytes")
        return await actual_upload(key, data, content_type)

    monkeypatch.setattr(local_storage, "upload", fail_after_partial_write)
    await worker(image.id, gallery.id)
    assert await _image_status(image.id) == ImageProcessingStatus.failed
    assert list(_files(local_storage, gallery)) == [image.storage_key]
    response = await _delete_gallery(client, gallery, owner, auth_headers, "frontend")
    assert response.status_code == 303
    assert _files(local_storage, gallery) == {}


async def test_failed_compensation_remains_recoverable_for_deletion(
    client, db, owner, auth_headers, local_storage, monkeypatch, worker
):
    gallery, image = await _pending_image(db, owner, local_storage)
    actual_upload, actual_delete = local_storage.upload, local_storage.delete
    attempted = []

    async def fail_upload(key, data, content_type):
        attempted.append(key)
        await actual_upload(key, io.BytesIO(b"partial"), content_type)
        raise OSError("partial preview")

    async def fail_cleanup(key):
        if key in attempted:
            raise OSError("cleanup unavailable")
        await actual_delete(key)

    monkeypatch.setattr(local_storage, "upload", fail_upload)
    monkeypatch.setattr(local_storage, "delete", fail_cleanup)
    await worker(image.id, gallery.id)
    assert await _image_status(image.id) == ImageProcessingStatus.failed
    assert len(_files(local_storage, gallery)) == 2
    async with TestSession() as fresh:
        assert await fresh.scalar(select(func.count()).select_from(ImagePreview)) == 0
    response = await _delete_gallery(client, gallery, owner, auth_headers, "api")
    assert response.status_code == 503
    assert await _image_status(image.id) == ImageProcessingStatus.failed
    monkeypatch.setattr(local_storage, "delete", actual_delete)
    response = await _delete_gallery(client, gallery, owner, auth_headers, "api")
    assert response.status_code == 204
    assert _files(local_storage, gallery) == {}


@pytest.mark.parametrize("acknowledged", [False, True])
async def test_commit_failure_does_not_destroy_acknowledged_ready_files(
    db, owner, local_storage, monkeypatch, worker, acknowledged
):
    gallery, image = await _pending_image(db, owner, local_storage)
    first = True

    def session_factory():
        nonlocal first
        session = TestSession()
        if first:
            first = False
            actual_commit = session.commit

            async def fail_commit():
                if acknowledged:
                    await actual_commit()
                raise OSError("Injected lost commit acknowledgement")

            monkeypatch.setattr(session, "commit", fail_commit)
        return session

    monkeypatch.setattr(preview_worker, "async_session", session_factory)
    await worker(image.id, gallery.id)
    assert await _image_status(image.id) == (
        ImageProcessingStatus.ready if acknowledged else ImageProcessingStatus.failed
    )
    assert len(_files(local_storage, gallery)) == (4 if acknowledged else 1)


async def test_cancellation_cleans_written_previews_and_propagates(db, owner, local_storage, monkeypatch, worker):
    gallery, image = await _pending_image(db, owner, local_storage)
    entered = asyncio.Event()
    calls = 0

    async def pause_second_render(fn, *args):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await asyncio.Event().wait()
        return fn(*args)

    monkeypatch.setattr(preview_worker, "run_in_pool", pause_second_render)
    task = asyncio.create_task(worker(image.id, gallery.id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert await _image_status(image.id) == ImageProcessingStatus.failed
    assert list(_files(local_storage, gallery)) == [image.storage_key]


@pytest.mark.parametrize("entrypoint", ["frontend", "api"])
async def test_gallery_delete_waits_for_running_worker(
    client, db, owner, auth_headers, local_storage, monkeypatch, worker, entrypoint
):
    gallery, image = await _pending_image(db, owner, local_storage)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_render(fn, *args):
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        return fn(*args)

    monkeypatch.setattr(preview_worker, "run_in_pool", delayed_render)
    task = asyncio.create_task(worker(image.id, gallery.id))
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        deletion = asyncio.create_task(_delete_gallery(client, gallery, owner, auth_headers, entrypoint))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(deletion), 0.1)
    finally:
        release.set()
        await asyncio.wait_for(task, 10)
    response = await asyncio.wait_for(deletion, 10)
    assert response.status_code == (204 if entrypoint == "api" else 303)
    assert _files(local_storage, gallery) == {}


async def test_worker_waiting_for_gallery_deletion_does_not_recreate_files(
    client, db, owner, auth_headers, local_storage, monkeypatch, worker
):
    gallery, image = await _pending_image(db, owner, local_storage)
    entered, release = asyncio.Event(), asyncio.Event()
    actual_delete = local_storage.delete
    first = True

    async def pause_first_delete(key):
        nonlocal first
        if first:
            first = False
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        await actual_delete(key)

    monkeypatch.setattr(local_storage, "delete", pause_first_delete)
    deletion = asyncio.create_task(_delete_gallery(client, gallery, owner, auth_headers, "frontend"))
    task = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task = asyncio.create_task(worker(image.id, gallery.id))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.1)
    finally:
        release.set()
        response = await asyncio.wait_for(deletion, 10)
    await asyncio.wait_for(task, 10)
    assert response.status_code == 303
    assert _files(local_storage, gallery) == {}


async def test_workers_for_different_images_still_run_concurrently(db, owner, local_storage, monkeypatch, worker):
    gallery, first = await _pending_image(db, owner, local_storage)
    _, second = await _pending_image(db, owner, local_storage, gallery)
    entered, release = asyncio.Event(), asyncio.Event()
    seen = 0

    async def both_enter(fn, *args):
        nonlocal seen
        seen += 1
        if seen == 2:
            entered.set()
        await asyncio.wait_for(release.wait(), 10)
        return fn(*args)

    monkeypatch.setattr(preview_worker, "run_in_pool", both_enter)
    tasks = [asyncio.create_task(worker(image.id, gallery.id)) for image in (first, second)]
    try:
        await asyncio.wait_for(entered.wait(), 5)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 10)
    assert await _image_status(first.id) == ImageProcessingStatus.ready
    assert await _image_status(second.id) == ImageProcessingStatus.ready
    assert len(_files(local_storage, gallery)) == 8


@pytest.mark.parametrize("entrypoint", ["single", "api_bulk", "frontend_bulk"])
async def test_image_deletion_uses_the_same_worker_fence(
    client, db, owner, auth_headers, local_storage, monkeypatch, worker, entrypoint
):
    gallery, image = await _pending_image(db, owner, local_storage)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_render(fn, *args):
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        return fn(*args)

    async def delete_image():
        if entrypoint == "single":
            return await client.delete(f"/api/v1/galleries/{gallery.id}/images/{image.id}", headers=auth_headers(owner))
        if entrypoint == "api_bulk":
            return await client.post(
                f"/api/v1/galleries/{gallery.id}/images/bulk-delete",
                json={"image_ids": [str(image.id)]},
                headers=auth_headers(owner),
            )
        session = create_access_token(str(owner.id))
        csrf = _mint_csrf_token(session)
        client.cookies.set("session", session)
        client.cookies.set("csrf_token", csrf)
        return await client.post(
            f"/galleries/{gallery.id}/bulk-delete",
            data={"image_ids": str(image.id), "csrf_token": csrf},
        )

    monkeypatch.setattr(preview_worker, "run_in_pool", delayed_render)
    task = asyncio.create_task(worker(image.id, gallery.id))
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        deletion = asyncio.create_task(delete_image())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(deletion), 0.1)
    finally:
        release.set()
        await asyncio.wait_for(task, 10)
    response = await asyncio.wait_for(deletion, 10)
    assert response.status_code == (204 if entrypoint == "single" else 200)
    assert _files(local_storage, gallery) == {}
    assert await _image_status(image.id) is None


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_concurrent_gallery_deletions_do_not_deadlock(
    client, db, owner, auth_headers, local_storage, monkeypatch, entrypoint
):
    gallery, _ = await _pending_image(db, owner, local_storage)
    entered, release = asyncio.Event(), asyncio.Event()
    actual_delete = local_storage.delete
    first = True

    async def pause_first_delete(key):
        nonlocal first
        if first:
            first = False
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        await actual_delete(key)

    monkeypatch.setattr(local_storage, "delete", pause_first_delete)
    deletion = asyncio.create_task(_delete_gallery(client, gallery, owner, auth_headers, entrypoint))
    other = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        other = asyncio.create_task(_delete_gallery(client, gallery, owner, auth_headers, entrypoint))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(other), 0.1)
    finally:
        release.set()
        response = await asyncio.wait_for(deletion, 10)
    second = await asyncio.wait_for(other, 10)
    assert response.status_code == (204 if entrypoint == "api" else 303)
    assert second.status_code == 404
    assert _files(local_storage, gallery) == {}


async def test_duplicate_workers_publish_only_once(db, owner, local_storage, monkeypatch, worker):
    gallery, image = await _pending_image(db, owner, local_storage)
    entered, release = asyncio.Event(), asyncio.Event()
    actual_upload = local_storage.upload
    keys = []

    async def record_upload(key, data, content_type):
        keys.append(key)
        return await actual_upload(key, data, content_type)

    async def pause_render(fn, *args):
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        return fn(*args)

    monkeypatch.setattr(local_storage, "upload", record_upload)
    monkeypatch.setattr(preview_worker, "run_in_pool", pause_render)
    task = asyncio.create_task(worker(image.id, gallery.id))
    duplicate = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        duplicate = asyncio.create_task(worker(image.id, gallery.id))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(duplicate), 0.1)
    finally:
        release.set()
        await asyncio.wait_for(task, 10)
    await asyncio.wait_for(duplicate, 10)
    assert len(keys) == 3
    assert await _image_status(image.id) == ImageProcessingStatus.ready
    assert len(_files(local_storage, gallery)) == 4


async def test_user_deletion_retains_all_references_after_later_gallery_failure(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch
):
    from app.db.models import User, UserStatus

    admin = User(email="storage-admin@example.com", password_hash="unused", status=UserStatus.admin)
    db.add(admin)
    await db.commit()
    galleries = sorted(
        [await _create_gallery(db, owner, "First"), await _create_gallery(db, owner, "Second")], key=lambda g: g.id
    )
    for gallery in galleries:
        await _upload_image(client, gallery, owner, auth_headers)
    key = next(key for key in _files(local_storage, galleries[1]) if "/originals/" in key)
    actual_delete = local_storage.delete

    async def fail_second_gallery(candidate):
        if candidate == key:
            raise OSError("second gallery unavailable")
        await actual_delete(candidate)

    monkeypatch.setattr(local_storage, "delete", fail_second_gallery)
    response = await client.delete(f"/api/v1/admin/users/{owner.id}", headers=auth_headers(admin))
    assert response.status_code == 503
    assert await verify_db.get(User, owner.id) is not None
    assert await verify_db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == owner.id)) == 2
    assert await verify_db.scalar(select(func.count()).select_from(Image)) == 2
    assert await verify_db.scalar(select(func.count()).select_from(ImagePreview)) == 6
    monkeypatch.setattr(local_storage, "delete", actual_delete)
    response = await client.delete(f"/api/v1/admin/users/{owner.id}", headers=auth_headers(admin))
    assert response.status_code == 204
    assert (await verify_db.execute(select(User).where(User.id == owner.id))).scalar_one_or_none() is None
    assert all(_files(local_storage, gallery) == {} for gallery in galleries)
    assert await verify_db.get(User, admin.id) is not None


async def test_gallery_delete_waits_for_upload_commit(client, db, owner, auth_headers, local_storage, monkeypatch):
    gallery = await _create_gallery(db, owner, "Concurrent upload")
    entered, release = asyncio.Event(), asyncio.Event()
    actual_upload = local_storage.upload
    first = True

    async def pause_original(key, data, content_type):
        nonlocal first
        if first:
            first = False
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
        return await actual_upload(key, data, content_type)

    monkeypatch.setattr(local_storage, "upload", pause_original)
    upload = asyncio.create_task(_upload_image(client, gallery, owner, auth_headers))
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        deletion = asyncio.create_task(_delete_gallery(client, gallery, owner, auth_headers, "api"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(deletion), 0.1)
    finally:
        release.set()
        await asyncio.wait_for(upload, 10)
    assert (await asyncio.wait_for(deletion, 10)).status_code == 204
    assert _files(local_storage, gallery) == {}


@pytest.mark.parametrize(("entrypoint", "unknown_count"), [("api_bulk", 1024), ("frontend_bulk", 998)])
async def test_bulk_unknown_ids_do_not_allocate_unbounded_advisory_locks(
    client, db, owner, auth_headers, local_storage, monkeypatch, entrypoint, unknown_count
):
    from sqlalchemy import text

    gallery, image = await _pending_image(db, owner, local_storage)
    # URL-encoded forms allow 1000 fields: 999 image IDs plus the CSRF token.
    # The JSON API has no form-field ceiling and keeps the larger test batch.
    ids = [str(image.id)] + [str(uuid.uuid4()) for _ in range(unknown_count)]
    actual_delete = local_storage.delete
    observed = []

    async def check_lock_footprint(key):
        async with TestSession() as fresh:
            observed.append(
                await fresh.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                    )
                )
            )
        await actual_delete(key)

    monkeypatch.setattr(local_storage, "delete", check_lock_footprint)
    if entrypoint == "api_bulk":
        response = await client.post(
            f"/api/v1/galleries/{gallery.id}/images/bulk-delete",
            json={"image_ids": ids},
            headers=auth_headers(owner),
        )
    else:
        session = create_access_token(str(owner.id))
        csrf = _mint_csrf_token(session)
        client.cookies.set("session", session)
        client.cookies.set("csrf_token", csrf)
        response = await client.post(
            f"/galleries/{gallery.id}/bulk-delete",
            data={"image_ids": ids, "csrf_token": csrf},
        )
    assert response.status_code == 200, response.text
    assert observed and max(observed) == 1
    assert await _image_status(image.id) is None
    assert _files(local_storage, gallery) == {}
