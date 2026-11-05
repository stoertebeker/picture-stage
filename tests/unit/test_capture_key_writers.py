"""Each metadata producer uses the same persisted capture-date ordering key."""

import uuid
from datetime import datetime
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app.db.models import CurrentSelection, Image, SelectionEvent
from app.frontend import galleries as frontend
from app.galleries import router as galleries
from app.galleries.schemas import GalleryDuplicateRequest
from app.images import process_pool
from app.images import router as images

EXIF = {"DateTimeOriginal": "2024:06:15 14:30:00"}
EXPECTED_DATE = datetime(2024, 6, 15, 14, 30)


@pytest.mark.parametrize("surface", ["api", "frontend"])
async def test_upload_writers_persist_capture_key(monkeypatch, surface):
    gallery = SimpleNamespace(id=uuid.uuid4(), watermark_config=None)
    user = SimpleNamespace(id=uuid.uuid4())
    db, batch, storage = AsyncMock(), AsyncMock(), MagicMock()
    batch.__aenter__.return_value = batch
    added = []
    db.add = added.append

    async def run(function, *args):
        if function.__name__ == "extract_image_metadata":
            return "a" * 64, 100, 100, dict(EXIF)
        return b"preview", 100, 100

    module = images if surface == "api" else frontend
    monkeypatch.setattr(module, "lock_gallery", AsyncMock())
    monkeypatch.setattr(module, "UploadBatch", lambda *args: batch)
    file = SpooledTemporaryFile(max_size=1024)
    file.write(b"original")
    file.seek(0)
    upload = UploadFile(file, filename="date.jpg", headers=Headers({"content-type": "image/jpeg"}))
    if surface == "api":
        monkeypatch.setattr(images, "run_in_pool", run)
        monkeypatch.setattr(images, "_image_to_response", lambda *args: SimpleNamespace())
        # Avoid a response schema double by supplying the real image response.
        monkeypatch.setattr(images, "ImageUploadResponse", lambda **kwargs: kwargs)
        db.execute.side_effect = [
            MagicMock(scalar_one_or_none=lambda: gallery),
            MagicMock(scalar_one_or_none=lambda: gallery),
            MagicMock(scalar=lambda: 0),
            MagicMock(scalar_one=lambda: next(item for item in added if isinstance(item, Image))),
        ]
        await images.upload_images(gallery.id, [upload], user=user, db=db, storage=storage)
    else:
        monkeypatch.setattr(process_pool, "run_in_pool", run)
        monkeypatch.setattr(frontend, "_get_owned_gallery", AsyncMock(return_value=gallery))
        monkeypatch.setattr(frontend, "_load_images_with_signed_urls", AsyncMock(return_value=[]))
        monkeypatch.setattr(frontend, "_build_context", lambda *args: {})
        monkeypatch.setattr(frontend, "templates", SimpleNamespace(TemplateResponse=lambda *args: HTMLResponse("ok")))
        db.execute.return_value = MagicMock(scalar=lambda: 0)
        await frontend.upload_images(
            gallery.id,
            Request({"type": "http", "headers": []}),
            BackgroundTasks(),
            [upload],
            user=user,
            db=db,
            storage=storage,
        )
    [image] = [item for item in added if isinstance(item, Image)]
    assert image.capture_sort_key == EXPECTED_DATE
    # A metadata replacement, including future worker writes, cannot stale the key.
    image.exif = {"DateTime": "2020:01:02 03:04:05"}
    assert image.capture_sort_key == datetime(2020, 1, 2, 3, 4, 5)


async def test_duplicate_derives_capture_key_and_starts_unrated(monkeypatch):
    source = SimpleNamespace(id=uuid.uuid4(), name="Source", guest_message=None, watermark_config=None)
    original = Image(
        id=uuid.uuid4(),
        gallery_id=source.id,
        storage_key="original.jpg",
        filename="date.jpg",
        content_type="image/jpeg",
        exif=EXIF,
        sort_order=1,
    )
    original.previews = []
    db, batch = AsyncMock(), AsyncMock()
    added = []
    db.add = added.append
    db.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: [original]))
    batch.__aenter__.return_value = batch
    monkeypatch.setattr(galleries, "lock_gallery", AsyncMock())
    monkeypatch.setattr(galleries, "reserve_gallery_slot", AsyncMock())
    monkeypatch.setattr(galleries, "_get_owned_gallery", AsyncMock(return_value=source))
    monkeypatch.setattr(galleries, "_count_images", AsyncMock(return_value=1))
    monkeypatch.setattr(galleries, "_gallery_to_response", lambda gallery, count: gallery)
    monkeypatch.setattr(galleries, "UploadBatch", lambda *args, **kwargs: batch)
    await galleries.duplicate_gallery(
        source.id, GalleryDuplicateRequest(), user=SimpleNamespace(id=uuid.uuid4()), db=db, storage=MagicMock()
    )
    [duplicate] = [item for item in added if isinstance(item, Image)]
    assert duplicate.id != original.id
    assert duplicate.capture_sort_key == EXPECTED_DATE
    assert not any(isinstance(item, CurrentSelection | SelectionEvent) for item in added)
