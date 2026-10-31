"""Reject unsupported decoder formats on both image upload surfaces."""

import io

import pytest
from PIL import Image

from tests.unit.test_image_format_security import _JP2

from .test_gallery_storage_cleanup import _create_gallery, _files, _png_bytes
from .test_gallery_storage_cleanup import local_storage as local_storage
from .test_upload_batch_cleanup import _counts, _post
from .test_upload_batch_cleanup import background_calls as background_calls
from .test_upload_batch_cleanup import client as client


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
@pytest.mark.parametrize("claimed_type", ["image/jpeg", "image/png", "image/webp"])
async def test_mislabelled_jpeg2000_cannot_reach_storage_or_preview(
    client, db, owner, auth_headers, verify_db, local_storage, background_calls, entrypoint, claimed_type
):
    gallery = await _create_gallery(db, owner, "Decoder allowlist")
    files = [
        ("files", ("legitimate.png", _png_bytes(), "image/png")),
        ("files", ("mislabelled", _JP2, claimed_type)),
    ]
    response = await _post(client, owner, gallery, auth_headers, entrypoint, files)
    assert response.status_code == 422
    assert response.json()["detail"] == "Unsupported or invalid image. Use JPEG, PNG, or WebP."
    assert response.headers["X-Picture-Stage-Error"] == "invalid-image"
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_truncated_image_with_valid_header_rejects_entire_batch(
    client, db, owner, auth_headers, verify_db, local_storage, background_calls, entrypoint
):
    gallery = await _create_gallery(db, owner, "Truncated image")
    output = io.BytesIO()
    Image.new("RGB", (128, 128), "red").save(output, format="PNG")
    truncated = output.getvalue()[:41]
    assert Image.open(io.BytesIO(truncated)).size == (128, 128)
    files = [
        ("files", ("legitimate.png", _png_bytes(), "image/png")),
        ("files", ("truncated.png", truncated, "image/png")),
    ]
    response = await _post(client, owner, gallery, auth_headers, entrypoint, files)
    assert response.status_code == 422
    assert response.headers["X-Picture-Stage-Error"] == "invalid-image"
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []


@pytest.mark.parametrize("entrypoint", ["api", "frontend"])
async def test_worker_infrastructure_error_remains_a_server_error(
    client, db, owner, auth_headers, verify_db, local_storage, background_calls, monkeypatch, entrypoint
):
    from app.images import process_pool
    from app.images import router as image_router

    gallery = await _create_gallery(db, owner, "Worker error")

    async def unavailable(*_args):
        raise OSError("worker communication failed")

    monkeypatch.setattr(process_pool, "run_in_pool", unavailable)
    monkeypatch.setattr(image_router, "run_in_pool", unavailable)
    response = await _post(
        client, owner, gallery, auth_headers, entrypoint, [("files", ("ok.png", _png_bytes(), "image/png"))]
    )
    assert response.status_code == 500
    assert "X-Picture-Stage-Error" not in response.headers
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []


async def test_preview_decode_failure_is_422_and_cleans_batch(
    client, db, owner, auth_headers, verify_db, local_storage, background_calls, monkeypatch
):
    from app.images import router as image_router
    from app.images.processing import InvalidImageContentError

    gallery = await _create_gallery(db, owner, "Preview decode error")
    real_run = image_router.run_in_pool

    async def fail_preview(func, *args):
        if func.__name__ == "render_thumbnail_bytes":
            raise InvalidImageContentError("late decode failure")
        return await real_run(func, *args)

    monkeypatch.setattr(image_router, "run_in_pool", fail_preview)
    response = await _post(
        client, owner, gallery, auth_headers, "api", [("files", ("ok.png", _png_bytes(), "image/png"))]
    )
    assert response.status_code == 422
    assert response.headers["X-Picture-Stage-Error"] == "invalid-image"
    assert _files(local_storage, gallery) == {}
    assert await _counts(verify_db, gallery) == (0, 0)
    assert background_calls == []
