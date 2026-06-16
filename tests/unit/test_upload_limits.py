"""Unit tests for the shared upload input limits (picture-stage-fbq).

Covers the per-request file-count cap and the per-file bounded read, including the
"0 = disabled" escape hatch. Limits are patched to small values so the tests stay
fast and don't allocate real upload-sized payloads.
"""

import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.config import settings
from app.images.upload_limits import enforce_file_count, read_within_limit


def test_enforce_file_count_at_limit_ok() -> None:
    with patch_setting("max_files_per_upload", 5):
        enforce_file_count(5)  # equal to the cap is allowed (no raise)


def test_enforce_file_count_over_limit_raises_413() -> None:
    with patch_setting("max_files_per_upload", 5):
        with pytest.raises(HTTPException) as exc:
            enforce_file_count(6)
    assert exc.value.status_code == 413


def test_enforce_file_count_zero_disables_cap() -> None:
    with patch_setting("max_files_per_upload", 0):
        enforce_file_count(10_000)  # 0 = off, no raise


@pytest.mark.asyncio
async def test_read_within_limit_under_cap_returns_full_bytes() -> None:
    with patch_setting("max_upload_file_mb", 1):
        f = UploadFile(file=io.BytesIO(b"hello"), filename="x.jpg")
        data = await read_within_limit(f)
    assert data == b"hello"


@pytest.mark.asyncio
async def test_read_within_limit_over_cap_raises_413() -> None:
    with patch_setting("max_upload_file_mb", 1):
        oversized = b"x" * (1 * 1024 * 1024 + 1)  # 1 MB + 1 byte
        f = UploadFile(file=io.BytesIO(oversized), filename="big.jpg")
        with pytest.raises(HTTPException) as exc:
            await read_within_limit(f)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_read_within_limit_zero_disables_cap() -> None:
    with patch_setting("max_upload_file_mb", 0):
        payload = b"x" * (2 * 1024 * 1024)  # 2 MB
        f = UploadFile(file=io.BytesIO(payload), filename="big.jpg")
        data = await read_within_limit(f)
    assert len(data) == len(payload)


def patch_setting(name: str, value: int):
    """Small context manager wrapping unittest.mock.patch.object for settings."""
    from unittest.mock import patch

    return patch.object(settings, name, value)
