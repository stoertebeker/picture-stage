"""Batch failures keep every written object deleted or durably recoverable (2jb0)."""

import asyncio
import io
import threading
from types import SimpleNamespace

import aiofiles.threadpool
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth.tokens import create_access_token
from app.db.models import Gallery, Image, UploadCleanupIntent
from app.frontend import galleries as gallery_frontend
from app.galleries.lifecycle import lock_gallery
from app.images import preview_worker, upload_batch
from app.images import router as image_router
from app.images.upload_batch import UploadBatch, UploadPlan
from app.main import app
from app.security.middleware import _mint_csrf_token

from .test_gallery_storage_cleanup import _create_gallery, _delete_gallery, _files, _png_bytes, _upload_image
from .test_gallery_storage_cleanup import local_storage as local_storage


@pytest_asyncio.fixture
async def client():
    # Observe the real HTTP status after failure handlers and rollback finish.
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def background_calls(monkeypatch):
    calls = []

    async def preview(*args):
        calls.append(args)

    monkeypatch.setattr(preview_worker, "process_image_previews", preview)
    return calls


async def _post(client, owner, gallery, auth_headers, entrypoint, files):
    if entrypoint == "api":
        return await client.post(f"/api/v1/galleries/{gallery.id}/images", files=files, headers=auth_headers(owner))
    token = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    return await client.post(f"/galleries/{gallery.id}/upload", files=files, headers={"X-CSRF-Token": csrf})


async def _counts(db, gallery):
    return (
        await db.scalar(select(func.count()).select_from(Image).where(Image.gallery_id == gallery.id)),
        await db.scalar(
            select(func.count()).select_from(UploadCleanupIntent).where(UploadCleanupIntent.gallery_id == gallery.id)
        ),
    )


def _two_pngs():
    return [("files", (name, _png_bytes(), "image/png")) for name in ("first.png", "second.png")]


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_saturated_upload_is_rejected_without_storage_writes(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint
):
    from app.config import settings
    from app.images import process_pool

    gallery = await _create_gallery(db, owner, "Busy upload")
    monkeypatch.setattr(settings, "image_processing_workers", 1)
    async with process_pool.image_processing_slot():

        async def wait():
            async with process_pool.image_processing_slot():
                pass

        waiters = [asyncio.create_task(wait()) for _ in range(2)]
        try:
            for _ in range(100):
                if process_pool._waiting == 2:
                    break
                await asyncio.sleep(0.01)
            assert process_pool._waiting == 2
            response = await asyncio.create_task(_post(client, owner, gallery, auth_headers, entrypoint, _two_pngs()))
            assert response.status_code == 503
            assert response.headers["retry-after"] == "5"
            assert _files(local_storage, gallery) == {}
            assert await _counts(verify_db, gallery) == (0, 0)
            assert background_calls == []
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_upload_processing_uses_pool_and_timeout_cleans_batch(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint
):
    from app.images import process_pool

    gallery = await _create_gallery(db, owner, "Pool timeout")
    calls = []
    real_run = process_pool.run_in_pool

    async def run(func, *args):
        calls.append(func.__name__)
        if func.__name__ == "extract_image_metadata" and calls.count(func.__name__) == 2:
            raise TimeoutError("test timeout")
        return await real_run(func, *args)

    monkeypatch.setattr(process_pool, "run_in_pool", run)
    monkeypatch.setattr(image_router, "run_in_pool", run)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 500
    assert calls.count("extract_image_metadata") == 2
    if entrypoint == "api":
        assert calls.count("render_thumbnail_bytes") == 2
        assert calls.count("render_preview_bytes") == 1
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
@pytest.mark.parametrize("failure", ["type", "count", "decode", "size", "database"])
async def test_later_invalid_file_cleans_earlier_files_and_preserves_control(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint, failure
):
    gallery = await _create_gallery(db, owner, "Failing upload")
    control = await _create_gallery(db, owner, "Unrelated upload")
    await _upload_image(client, control, owner, auth_headers)
    retained = _files(local_storage, control)
    actual_upload = local_storage.upload
    writes = []

    async def record_upload(key, data, content_type):
        writes.append(key)
        return await actual_upload(key, data, content_type)

    monkeypatch.setattr(local_storage, "upload", record_upload)
    files = _two_pngs()
    if failure == "type":
        files[1] = ("files", ("invalid.txt", b"invalid", "text/plain"))
        expected = 422
    elif failure == "database":
        # Real PostgreSQL flush/transaction failure after storage succeeded.
        files[1] = ("files", ("x" * 513 + ".png", _png_bytes(), "image/png"))
        expected = 500
    elif failure in {"size", "count"}:
        from app.config import settings

        if failure == "size":
            monkeypatch.setattr(settings, "max_upload_file_mb", 1)
            files[1] = ("files", ("large.png", b"x" * (1024 * 1024 + 1), "image/png"))
        else:
            monkeypatch.setattr(settings, "max_files_per_upload", 1)
        expected = 413
    else:
        files[1] = ("files", ("invalid.png", b"not an image", "image/png"))
        expected = 422
    response = await _post(client, owner, gallery, auth_headers, entrypoint, files)
    assert response.status_code == expected, response.text
    assert _files(local_storage, gallery) == {}
    assert _files(local_storage, control) == retained
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []
    if failure in {"type", "count"}:
        assert writes == []


@pytest.mark.parametrize(
    "entrypoint,fail_at", [("api", 1), ("api", 2), ("api", 4), ("api", 5), ("frontend", 1), ("frontend", 2)]
)
async def test_raising_partial_storage_write_is_also_compensated(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint, fail_at
):
    gallery = await _create_gallery(db, owner, "Partial write")
    actual_upload = local_storage.upload
    writes = 0

    async def write_then_fail(key, data, content_type):
        nonlocal writes
        writes += 1
        if writes == fail_at:
            await actual_upload(key, io.BytesIO(b"partially written"), content_type)
            raise OSError("Storage write failed after producing bytes")
        return await actual_upload(key, data, content_type)

    monkeypatch.setattr(local_storage, "upload", write_then_fail)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 500
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_cleanup_failure_retains_durable_intent_and_gallery_delete_retries(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, entrypoint
):
    gallery = await _create_gallery(db, owner, "Retry failed cleanup")
    control = await _create_gallery(db, owner, "Do not remove")
    await _upload_image(client, control, owner, auth_headers)
    retained = _files(local_storage, control)
    actual_upload, actual_delete = local_storage.upload, local_storage.delete

    async def write_then_fail(key, data, content_type):
        await actual_upload(key, io.BytesIO(b"partial original"), content_type)
        raise OSError("Storage upload failed after a partial write")

    async def no_delete(key):
        if key.startswith(f"{gallery.id}/"):
            raise OSError("Cleanup storage unavailable")
        await actual_delete(key)

    monkeypatch.setattr(local_storage, "upload", write_then_fail)
    monkeypatch.setattr(local_storage, "delete", no_delete)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 500
    assert await _counts(verify_db, gallery) == (0, 1)
    remaining = _files(local_storage, gallery)
    assert len(remaining) == 1
    intent = await verify_db.scalar(select(UploadCleanupIntent).where(UploadCleanupIntent.gallery_id == gallery.id))
    assert set(remaining) <= {key for keys in intent.image_keys.values() for key in keys}
    failed_delete = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert failed_delete.status_code == 503
    await verify_db.rollback()
    assert await verify_db.get(Gallery, gallery.id) is not None
    assert await _counts(verify_db, gallery) == (0, 1)
    monkeypatch.setattr(local_storage, "delete", actual_delete)
    deleted = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert deleted.status_code == (204 if entrypoint == "api" else 303)
    await verify_db.rollback()
    assert await verify_db.get(Gallery, gallery.id) is None
    assert await _counts(verify_db, gallery) == (0, 0)
    assert _files(local_storage, gallery) == {}
    assert _files(local_storage, control) == retained


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
@pytest.mark.parametrize("ack_lost", [False, True])
async def test_image_commit_failure_does_not_destroy_an_acknowledged_late_commit(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint, ack_lost
):
    gallery = await _create_gallery(db, owner, "Commit outcome")
    enter = UploadBatch.__aenter__

    async def enter_with_commit_failure(batch):
        result = await enter(batch)
        commit = batch.db.commit

        async def fail_commit():
            if ack_lost:
                await commit()
            raise OSError("Commit acknowledgement lost" if ack_lost else "Commit failed")

        monkeypatch.setattr(batch.db, "commit", fail_commit)
        return result

    monkeypatch.setattr(UploadBatch, "__aenter__", enter_with_commit_failure)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 500
    assert await _counts(verify_db, gallery) == ((2, 0) if ack_lost else (0, 0))
    assert len(_files(local_storage, gallery)) == ((8 if entrypoint == "api" else 2) if ack_lost else 0)
    assert background_calls == []


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_cancelled_storage_write_is_cleaned_and_no_preview_is_dispatched(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint
):
    gallery = await _create_gallery(db, owner, "Cancelled upload")
    actual_upload = local_storage.upload

    started = asyncio.Event()
    finished = asyncio.Event()

    async def cancel_after_write(key, data, content_type):
        await actual_upload(key, io.BytesIO(b"cancelled original"), content_type)
        started.set()
        await finished.wait()
        return key

    monkeypatch.setattr(local_storage, "upload", cancel_after_write)
    request = asyncio.create_task(_post(client, owner, gallery, auth_headers, entrypoint, _two_pngs()))
    await asyncio.wait_for(started.wait(), timeout=10)
    request.cancel()
    await asyncio.sleep(0.05)
    finished.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []


async def test_repeated_direct_cancellation_keeps_gallery_fence_until_write_finishes(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch
):
    gallery = await _create_gallery(db, owner, "Repeated cancellation retains lifecycle fence")
    # This test intentionally runs the batch on its arrange session; its
    # rollback expires ORM instances while the concurrent delete removes them.
    gallery = SimpleNamespace(id=gallery.id)
    plan = UploadPlan.for_file(gallery.id, "image/png", previews=False)
    started, finish = asyncio.Event(), asyncio.Event()
    actual_upload = local_storage.upload

    async def slow_upload(key, data, content_type):
        started.set()
        await finish.wait()
        return await actual_upload(key, data, content_type)

    monkeypatch.setattr(local_storage, "upload", slow_upload)

    async def upload():
        await lock_gallery(db, gallery.id, shared=True)
        async with UploadBatch(db, local_storage, gallery.id, [plan]) as batch:
            assert await db.get(Gallery, gallery.id, populate_existing=True) is not None
            await batch.upload(plan.original_key, io.BytesIO(_png_bytes()), "image/png")
            await batch.commit()

    request = asyncio.create_task(upload())
    await asyncio.wait_for(started.wait(), timeout=10)
    request.cancel()
    await asyncio.sleep(0.05)
    request.cancel()
    await asyncio.sleep(0.05)
    assert not request.done()
    assert await _counts(verify_db, gallery) == (0, 1)
    deleted = asyncio.create_task(_delete_gallery(client, gallery, owner, auth_headers, "api"))
    await asyncio.sleep(0.05)
    assert not deleted.done(), "Deletion must wait while the storage write is still in flight"
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert (await deleted).status_code == 204
    await verify_db.rollback()
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_failure_after_intent_commit_before_write_retains_only_no_write_intent(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, entrypoint
):
    gallery = await _create_gallery(db, owner, "No write before owner recheck")
    actual_lock = upload_batch.lock_gallery

    async def fail_reacquire(*args, **kwargs):
        if kwargs.get("shared"):
            raise OSError("Lost connection after committing the intent")
        return await actual_lock(*args, **kwargs)

    monkeypatch.setattr(upload_batch, "lock_gallery", fail_reacquire)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 500
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 1)
    deleted = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert deleted.status_code == (204 if entrypoint == "api" else 303)
    await verify_db.rollback()
    assert await _counts(verify_db, gallery) == (0, 0)


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_deletion_between_intent_commit_and_upload_recheck_causes_no_write(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, entrypoint
):
    gallery = await _create_gallery(db, owner, "Deleted during intent handshake")
    actual_lock = upload_batch.lock_gallery

    async def delete_in_gap(session, gallery_id, *, shared=False):
        if shared:
            # This callback runs after the intent commit, before the new fence.
            deleted = await _delete_gallery(client, gallery, owner, auth_headers, "api")
            assert deleted.status_code == 204
        await actual_lock(session, gallery_id, shared=shared)

    monkeypatch.setattr(upload_batch, "lock_gallery", delete_in_gap)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 404
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
@pytest.mark.parametrize("filename", ["ä" * 180 + ".png", "folder/../../photo.weird", "photo." + "x" * 300])
async def test_legitimate_filenames_remain_metadata_not_storage_key_parts(
    client, db, owner, auth_headers, verify_db, local_storage, background_calls, entrypoint, filename
):
    gallery = await _create_gallery(db, owner, "Filename metadata")
    response = await _post(
        client, owner, gallery, auth_headers, entrypoint, [("files", (filename, _png_bytes(), "image/png"))]
    )
    assert response.status_code == (201 if entrypoint == "api" else 200), response.text
    image = await verify_db.scalar(select(Image).where(Image.gallery_id == gallery.id))
    assert image.filename == filename
    assert image.storage_key == f"{gallery.id}/originals/{image.id}.png"
    assert len(_files(local_storage, gallery)) == (4 if entrypoint == "api" else 1)
    assert await _counts(verify_db, gallery) == (1, 0)
    assert len(background_calls) == (0 if entrypoint == "api" else 1)


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_postcommit_response_failure_preserves_committed_files(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, entrypoint
):
    gallery = await _create_gallery(db, owner, "Response failed after commit")
    if entrypoint == "api":

        def fail_response(*args, **kwargs):
            raise RuntimeError("Response serialization failed after commit")

        monkeypatch.setattr(image_router, "ImageUploadResponse", fail_response)
    else:

        async def fail_grid(*args, **kwargs):
            raise RuntimeError("Grid rendering failed after commit")

        monkeypatch.setattr(gallery_frontend, "_load_images_with_signed_urls", fail_grid)
    response = await _post(client, owner, gallery, auth_headers, entrypoint, _two_pngs())
    assert response.status_code == 500
    assert await _counts(verify_db, gallery) == (2, 0)
    assert len(_files(local_storage, gallery)) == (8 if entrypoint == "api" else 2)


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_unreadable_recovery_database_keeps_intent_instead_of_guessing(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, entrypoint
):
    gallery = await _create_gallery(db, owner, "Recovery connection unavailable")
    enter = UploadBatch.__aenter__

    def unavailable():
        raise OSError("Recovery database unavailable")

    async def enter_without_recovery_connection(batch):
        result = await enter(batch)
        batch._sessions = unavailable
        return result

    monkeypatch.setattr(UploadBatch, "__aenter__", enter_without_recovery_connection)
    files = _two_pngs()
    files[1] = ("files", ("invalid.png", b"not an image", "image/png"))
    response = await _post(client, owner, gallery, auth_headers, entrypoint, files)
    assert response.status_code == 422
    assert await _counts(verify_db, gallery) == (0, 1)
    assert len(_files(local_storage, gallery)) == (4 if entrypoint == "api" else 1)
    deleted = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert deleted.status_code == (204 if entrypoint == "api" else 303)
    await verify_db.rollback()
    assert await _counts(verify_db, gallery) == (0, 0)
    assert _files(local_storage, gallery) == {}


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_cancel_waits_for_inflight_open_before_cleanup(
    client, db, owner, auth_headers, verify_db, local_storage, monkeypatch, background_calls, entrypoint
):
    gallery = await _create_gallery(db, owner, "Cancellation while opening original")
    loop = asyncio.get_running_loop()
    opening = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    actual_open = aiofiles.threadpool.sync_open

    def blocked_open(file, *args, **kwargs):
        if str(file).startswith(str(local_storage.base_path / str(gallery.id))):
            loop.call_soon_threadsafe(opening.set)
            release.wait(timeout=10)
            try:
                return actual_open(file, *args, **kwargs)
            finally:
                finished.set()
        return actual_open(file, *args, **kwargs)

    monkeypatch.setattr(aiofiles.threadpool, "sync_open", blocked_open)
    request = asyncio.create_task(_post(client, owner, gallery, auth_headers, entrypoint, _two_pngs()))
    await asyncio.wait_for(opening.wait(), timeout=10)
    request.cancel()
    # Allow cancellation/cleanup to race against an already running OS thread.
    # A correct implementation must keep the fence until the write is quiescent.
    try:
        await asyncio.wait_for(asyncio.shield(request), timeout=0.2)
    except (TimeoutError, asyncio.CancelledError):
        pass
    finally:
        release.set()
    try:
        await request
    except asyncio.CancelledError:
        pass
    assert await asyncio.to_thread(finished.wait, 10)
    counts = await _counts(verify_db, gallery)
    assert _files(local_storage, gallery) == {}, f"Late write remained with Image/Intent counts {counts}"
    assert counts == (0, 0)
    assert background_calls == []
