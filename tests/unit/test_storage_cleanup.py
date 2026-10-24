"""Storage cleanup must remove known files without losing retryable failures."""

import asyncio
import io
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

from app.db.models import Image, ImagePreview, PreviewVariant
from app.images.storage_cleanup import (
    StorageCleanupError,
    canonical_preview_keys,
    delete_storage_keys,
    image_storage_keys,
)
from app.storage.local import LocalStorage


@pytest.fixture
def image() -> Image:
    gallery_id = uuid.uuid4()
    image_id = uuid.uuid4()
    return Image(
        id=image_id,
        gallery_id=gallery_id,
        storage_key=f"{gallery_id}/originals/{image_id}.jpg",
        filename="portrait.jpg",
        previews=[
            ImagePreview(
                variant=PreviewVariant.thumb_sm,
                storage_key=f"{gallery_id}/previews/{image_id}_thumb_sm.webp",
                width=320,
                height=240,
            ),
            ImagePreview(
                variant=PreviewVariant.preview,
                storage_key=f"{gallery_id}/legacy-previews/portrait-large.webp",
                width=1600,
                height=1200,
            ),
        ],
    )


def test_canonical_preview_keys_include_every_variant(image: Image) -> None:
    expected = {
        f"{image.gallery_id}/previews/{image.id}_thumb_sm.webp",
        f"{image.gallery_id}/previews/{image.id}_thumb_md.webp",
        f"{image.gallery_id}/previews/{image.id}_preview.webp",
    }

    assert canonical_preview_keys(image.gallery_id, image.id) == expected
    assert len(expected) == len(PreviewVariant)


def test_image_storage_keys_include_original_legacy_and_unregistered_previews(image: Image) -> None:
    assert image_storage_keys(image) == {
        image.storage_key,
        f"{image.gallery_id}/legacy-previews/portrait-large.webp",
        f"{image.gallery_id}/previews/{image.id}_thumb_sm.webp",
        f"{image.gallery_id}/previews/{image.id}_thumb_md.webp",
        f"{image.gallery_id}/previews/{image.id}_preview.webp",
    }


def test_image_storage_keys_include_previews_without_database_records(image: Image) -> None:
    image.previews = []

    assert image_storage_keys(image) == {
        image.storage_key,
        f"{image.gallery_id}/previews/{image.id}_thumb_sm.webp",
        f"{image.gallery_id}/previews/{image.id}_thumb_md.webp",
        f"{image.gallery_id}/previews/{image.id}_preview.webp",
    }


async def test_delete_storage_keys_removes_files_and_accepts_missing_keys(tmp_path: Path, image: Image) -> None:
    storage = LocalStorage(str(tmp_path))
    keys = image_storage_keys(image)
    missing_key = f"{image.gallery_id}/previews/{image.id}_thumb_md.webp"
    for key in keys - {missing_key}:
        await storage.upload(key, io.BytesIO(b"image contents"), "image/jpeg")
    unrelated_key = f"{uuid.uuid4()}/originals/keep.jpg"
    await storage.upload(unrelated_key, io.BytesIO(b"unrelated gallery"), "image/jpeg")

    await delete_storage_keys(storage, keys)
    await delete_storage_keys(storage, keys)

    assert all(not (tmp_path / key).exists() for key in keys)
    assert (tmp_path / unrelated_key).read_bytes() == b"unrelated gallery"


@pytest.mark.parametrize("failure_type", [PermissionError, RuntimeError])
async def test_delete_storage_keys_retains_failure_and_can_retry(
    tmp_path: Path,
    image: Image,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_type: type[Exception],
) -> None:
    # Alembic changes global logger state in the full suite. Capture this
    # helper independently of that unrelated configuration side effect.
    monkeypatch.setattr(logging.getLogger("app.images.storage_cleanup"), "disabled", False)
    storage = LocalStorage(str(tmp_path))
    keys = image_storage_keys(image)
    for key in keys:
        await storage.upload(key, io.BytesIO(b"retryable image"), "image/jpeg")
    failed_key = sorted(keys)[1]
    original_delete = storage.delete
    fail = True
    attempts: list[str] = []

    async def delete_with_failure(key: str) -> None:
        attempts.append(key)
        if fail and key == failed_key:
            raise failure_type("storage temporarily unavailable")
        await original_delete(key)

    monkeypatch.setattr(storage, "delete", delete_with_failure)

    with pytest.raises(StorageCleanupError) as exc_info:
        await delete_storage_keys(storage, keys)

    assert exc_info.value.failed_keys == (failed_key,)
    assert attempts == sorted(keys)
    assert (tmp_path / failed_key).read_bytes() == b"retryable image"
    assert all(not (tmp_path / key).exists() for key in keys - {failed_key})
    assert any(failed_key in record.getMessage() for record in caplog.records)

    fail = False
    attempts.clear()
    await delete_storage_keys(storage, keys)

    assert attempts == sorted(keys)
    assert all(not (tmp_path / key).exists() for key in keys)


async def test_delete_storage_keys_deduplicates_and_sorts_iterables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalStorage(str(tmp_path))
    delete = AsyncMock(wraps=storage.delete)
    monkeypatch.setattr(storage, "delete", delete)

    await delete_storage_keys(storage, iter(["c.webp", "a.jpg", "c.webp", "b.webp", "a.jpg"]))

    assert delete.await_args_list == [call("a.jpg"), call("b.webp"), call("c.webp")]


async def test_delete_storage_keys_reports_all_failures_after_attempting_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalStorage(str(tmp_path))
    delete = AsyncMock(side_effect=[OSError("first failure"), None, RuntimeError("second failure")])
    monkeypatch.setattr(storage, "delete", delete)

    with pytest.raises(StorageCleanupError) as exc_info:
        await delete_storage_keys(storage, ["c.webp", "a.jpg", "b.webp", "a.jpg"])

    assert exc_info.value.failed_keys == ("a.jpg", "c.webp")
    assert delete.await_args_list == [call("a.jpg"), call("b.webp"), call("c.webp")]


async def test_delete_storage_keys_propagates_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = LocalStorage(str(tmp_path))
    delete = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(storage, "delete", delete)

    with pytest.raises(asyncio.CancelledError):
        await delete_storage_keys(storage, ["a.jpg", "b.webp"])

    assert delete.await_args_list == [call("a.jpg")]
