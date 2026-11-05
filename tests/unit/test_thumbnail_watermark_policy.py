"""Product policy: only the large guest preview carries a watermark (teis).

Exercise both producers with real Pillow rendering and inspect stored pixels;
only persistence, locking, and process scheduling are replaced by test doubles.
"""

import io
import uuid
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, UploadFile
from PIL import Image as PILImage
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.db.models import Image, ImagePreview, ImageProcessingStatus, PreviewVariant
from app.frontend import guest as guest_frontend
from app.guest import router as guest_router
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.guest.service import GuestImagePage
from app.images import preview_worker
from app.images import router as image_router
from app.storage.dependencies import get_storage


@pytest.mark.parametrize("producer", ["api", "worker"])
@pytest.mark.parametrize("config", [None, {}, {"enabled": True}, {"enabled": False}])
async def test_only_large_preview_is_watermarked(monkeypatch, producer, config) -> None:
    # A solid black source makes any added white text measurable, independent of
    # the exact font, glyph positions, or WebP byte representation.
    source = io.BytesIO()
    PILImage.new("RGB", (1600, 900), "black").save(source, "PNG")
    original = source.getvalue()
    gallery_id = uuid.uuid4()
    image_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    gallery = SimpleNamespace(id=gallery_id, watermark_config=config)
    stored: dict[str, bytes] = {}
    added: list[object] = []

    monkeypatch.setattr(settings, "watermark_text", "WM")
    monkeypatch.setattr(settings, "watermark_opacity", 1.0)
    monkeypatch.setattr(settings, "watermark_position", "center")
    monkeypatch.setattr(settings, "watermark_font_size", 100)

    async def render(function, *args):
        return function(*args)

    async def capture(key, data, content_type):
        stored[key] = data.read()
        return key

    db = AsyncMock()
    db.add = added.append
    storage = MagicMock()

    if producer == "api":
        batch = AsyncMock()
        batch.__aenter__.return_value = batch
        batch.upload.side_effect = capture
        monkeypatch.setattr(image_router, "UploadBatch", lambda *args: batch)
        monkeypatch.setattr(image_router, "lock_gallery", AsyncMock())
        monkeypatch.setattr(image_router, "run_in_pool", render)

        def loaded_image():
            image = next(item for item in added if isinstance(item, Image))
            image.created_at = datetime.now(UTC)
            image.previews = [item for item in added if isinstance(item, ImagePreview)]
            return image

        db.execute.side_effect = [
            MagicMock(scalar_one_or_none=lambda: gallery),
            MagicMock(scalar_one_or_none=lambda: gallery),
            MagicMock(scalar=lambda: 0),
            MagicMock(scalar_one=loaded_image),
        ]
        with SpooledTemporaryFile(max_size=len(original) + 1) as file:
            file.write(original)
            file.seek(0)
            result = await image_router.upload_images(
                gallery_id,
                [UploadFile(file, filename="black.png", headers=Headers({"content-type": "image/png"}))],
                user=SimpleNamespace(id=owner_id),
                db=db,
                storage=storage,
            )
        assert result.uploaded == 1
        batch.commit.assert_awaited_once()
        originals = {key: data for key, data in stored.items() if "/originals/" in key}
        assert list(originals.values()) == [original]
    else:
        image = Image(
            id=image_id,
            gallery_id=gallery_id,
            storage_key=f"{gallery_id}/originals/{image_id}.png",
            filename="black.png",
            exif={"DateTimeOriginal": "2024:06:15 14:30:00"},
            processing_status=ImageProcessingStatus.pending,
        )
        db.__aenter__.return_value = db
        db.execute.return_value = MagicMock(scalar_one_or_none=lambda: image)
        db.scalar.return_value = owner_id

        async def read_original(key):
            assert key == image.storage_key
            yield original

        async def capture_worker(db, storage, owner_id, gallery_id, image_id, key, data, content_type):
            return await capture(key, data, content_type)

        storage.download_stream = read_original
        monkeypatch.setattr(preview_worker, "async_session", lambda: db)
        monkeypatch.setattr(preview_worker, "get_storage", lambda: storage)
        monkeypatch.setattr(preview_worker, "lock_gallery", AsyncMock())
        monkeypatch.setattr(preview_worker, "lock_images", AsyncMock())
        monkeypatch.setattr(preview_worker, "upload", capture_worker)
        monkeypatch.setattr(preview_worker, "run_in_pool", render)
        await preview_worker.process_image_previews(image_id, gallery_id, config)
        assert image.processing_status == ImageProcessingStatus.ready
        assert image.capture_sort_key == datetime(2024, 6, 15, 14, 30)
        db.commit.assert_awaited_once()
        assert all("/previews/" in key for key in stored)

    previews = [item for item in added if isinstance(item, ImagePreview)]
    assert {item.variant for item in previews} == set(PreviewVariant)
    expected_sizes = {"thumb_sm": (320, 180), "thumb_md": (640, 360), "preview": (1280, 720)}
    for preview in previews:
        with PILImage.open(io.BytesIO(stored[preview.storage_key])) as rendered:
            assert rendered.size == expected_sizes[preview.variant.value]
            assert (preview.width, preview.height) == rendered.size
            brightest = rendered.convert("L").getextrema()[1]
        if preview.variant == PreviewVariant.preview and (config or {}).get("enabled") is not False:
            assert brightest > 120
        else:
            assert brightest < 30


@pytest.mark.parametrize("consumer", ["api", "frontend"])
async def test_guest_variants_are_accessible_without_exposing_original(monkeypatch, consumer) -> None:
    gallery = SimpleNamespace(id=uuid.uuid4())
    image = SimpleNamespace(
        id=uuid.uuid4(), filename="black.png", sort_order=0, storage_key=f"{gallery.id}/originals/private.png"
    )
    image.previews = [
        ImagePreview(variant=variant, storage_key=f"{gallery.id}/previews/{variant.value}.webp")
        for variant in PreviewVariant
    ]
    db = AsyncMock()
    consumer_module = guest_router if consumer == "api" else guest_frontend
    if consumer == "api":
        monkeypatch.setattr(
            consumer_module,
            "load_guest_image_page",
            AsyncMock(return_value=GuestImagePage([image], {}, None, 1, 0, 0, 1)),
        )
    else:
        monkeypatch.setattr(consumer_module, "load_sorted_filtered_images", AsyncMock(return_value=([image], {})))
    if consumer == "api":
        monkeypatch.setattr(guest_router, "resolve_gallery_by_token", AsyncMock(return_value=gallery))
        monkeypatch.setattr(guest_router, "check_gallery_accessible", lambda gallery: None)
        monkeypatch.setattr(guest_router, "require_gallery_read_access", AsyncMock(return_value=gallery))
        [response] = await guest_router.list_shared_images(
            "capability",
            request=Request({"type": "http", "headers": []}),
            response=Response(),
            sort_by=ImageSortBy.sort_order,
            sort_dir=SortDirection.asc,
            filter=ImageFilter.all,
            cursor=None,
            offset=0,
            db=db,
        )
        fields = response.model_dump()
    else:
        [fields] = await guest_frontend._load_images(gallery, db)

    urls = [fields[name] for name in ("thumb_sm_url", "thumb_md_url", "preview_url")]
    assert {urlsplit(url).path for url in urls} == {f"/media/{preview.storage_key}" for preview in image.previews}
    assert all("original" not in name for name in fields)

    storage = MagicMock()
    storage.exists = AsyncMock(return_value=True)

    async def download(key):
        yield key.encode()

    storage.download_stream = download
    app = FastAPI()
    app.include_router(image_router.router)

    async def test_storage():
        return storage

    app.dependency_overrides[get_storage] = test_storage
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for url in urls:
            response = await client.get(url)
            assert response.status_code == 200
            assert response.content == urlsplit(url).path.removeprefix("/media/").encode()
            # A valid thumbnail/preview grant cannot authorize the original path.
            stolen_grant = await client.get(f"/media/{image.storage_key}?{urlsplit(url).query}")
            assert stolen_grant.status_code == 403
        assert (await client.get(f"/media/{image.storage_key}")).status_code == 422
    assert storage.exists.await_count == 3
