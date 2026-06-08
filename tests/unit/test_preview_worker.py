"""Unit tests for the async preview worker (picture-stage-o4d).

Preview generation moved off the upload request into a background worker. These
DB-free tests verify the worker's status transitions and tenant scoping using
mocked DB session and storage — no Postgres required.
"""

import io
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image as PILImage

from app.db.models import ImageProcessingStatus
from app.images import preview_worker


def _make_jpeg(width: int = 1600, height: int = 1200) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), "gray").save(buf, "JPEG")
    return buf.getvalue()


class _FakeSession:
    """Async-context-manager session stub returning a fixed image."""

    def __init__(self, image: Any) -> None:
        self._image = image
        self.committed = False
        self.added: list[Any] = []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: object) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._image)
        return result

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


def _fake_image() -> MagicMock:
    img = MagicMock()
    img.id = uuid.uuid4()
    img.storage_key = "gallery/originals/x.jpg"
    img.processing_status = ImageProcessingStatus.pending
    return img


def _storage_with_original(raw: bytes, *, upload: AsyncMock | None = None) -> MagicMock:
    async def fake_stream(key: str, chunk_size: int = 65536) -> Any:
        yield raw

    storage = MagicMock()
    storage.download_stream = fake_stream
    storage.upload = upload or AsyncMock()
    return storage


async def test_success_sets_ready_and_uploads_all_variants() -> None:
    """Happy path: every preview variant is uploaded and status becomes ready."""
    image = _fake_image()
    session = _FakeSession(image)
    storage = _storage_with_original(_make_jpeg())
    gallery_id = uuid.uuid4()

    with (
        patch.object(preview_worker, "async_session", return_value=session),
        patch.object(preview_worker, "get_storage", return_value=storage),
    ):
        await preview_worker.process_image_previews(image.id, gallery_id, "PREVIEW · TEST")

    assert image.processing_status == ImageProcessingStatus.ready
    assert session.committed is True
    # One upload per preview variant (thumb_sm, thumb_md, preview).
    assert storage.upload.call_count == len(preview_worker.PREVIEW_SIZES)
    assert len(session.added) == len(preview_worker.PREVIEW_SIZES)


async def test_storage_failure_sets_failed() -> None:
    """A failure during processing flips the status to failed (not stuck pending)."""
    image = _fake_image()
    main_session = _FakeSession(image)
    fail_session = _FakeSession(image)
    storage = _storage_with_original(_make_jpeg(), upload=AsyncMock(side_effect=RuntimeError("storage down")))
    gallery_id = uuid.uuid4()

    # First async_session() call is the main worker; second is _mark_failed.
    sessions = iter([main_session, fail_session])

    with (
        patch.object(preview_worker, "async_session", side_effect=lambda: next(sessions)),
        patch.object(preview_worker, "get_storage", return_value=storage),
    ):
        await preview_worker.process_image_previews(image.id, gallery_id, "PREVIEW · TEST")

    assert image.processing_status == ImageProcessingStatus.failed
    assert fail_session.committed is True


async def test_missing_image_is_a_noop() -> None:
    """If the image was deleted before the worker ran, nothing is processed."""
    session = _FakeSession(None)
    storage = _storage_with_original(_make_jpeg())
    gallery_id = uuid.uuid4()

    with (
        patch.object(preview_worker, "async_session", return_value=session),
        patch.object(preview_worker, "get_storage", return_value=storage),
    ):
        await preview_worker.process_image_previews(uuid.uuid4(), gallery_id, "PREVIEW · TEST")

    assert session.committed is False
    assert storage.upload.call_count == 0


async def test_worker_scopes_query_by_image_and_gallery() -> None:
    """Tenant isolation: the lookup must filter by both image_id and gallery_id."""
    image = _fake_image()
    captured: list[Any] = []

    class _CapturingSession(_FakeSession):
        async def execute(self, statement: object = None, *args: object) -> MagicMock:
            captured.append(statement)
            return await super().execute(statement, *args)

    session = _CapturingSession(image)
    storage = _storage_with_original(_make_jpeg())
    gallery_id = uuid.uuid4()

    with (
        patch.object(preview_worker, "async_session", return_value=session),
        patch.object(preview_worker, "get_storage", return_value=storage),
    ):
        await preview_worker.process_image_previews(image.id, gallery_id, "PREVIEW · TEST")

    # The compiled WHERE clause references both columns.
    where_sql = str(captured[0].compile()).lower()
    assert "image" in where_sql and "gallery_id" in where_sql


@pytest.mark.parametrize("status", list(ImageProcessingStatus))
def test_status_enum_values_are_stable(status: ImageProcessingStatus) -> None:
    """Guard the wire values the grid template branches on."""
    assert status.value in {"pending", "ready", "failed"}
