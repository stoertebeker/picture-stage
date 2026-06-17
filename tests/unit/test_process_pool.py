"""Tests for the preview process pool with a hard per-file timeout (picture-stage-q9td).

Covers the picklable Pillow wrappers, the spawn-pool roundtrip (pickling + worker
re-import), the *hard* timeout (a hung task is killed and surfaces TimeoutError),
and that the decompression-bomb pixel cap is re-applied inside spawn workers. The
pool starts real worker processes, so a module-scoped fixture tears it down.
"""

import io

import pytest
from PIL import Image

from app.config import settings
from app.images.process_pool import run_in_pool, shutdown_pool
from app.images.processing import render_preview_bytes, render_thumbnail_bytes
from tests.unit.pool_helpers import add, get_max_image_pixels, sleep_seconds


@pytest.fixture(scope="module", autouse=True)
def _cleanup_pool():
    yield
    shutdown_pool()


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (90, 20, 20)).save(buf, format="PNG")
    return buf.getvalue()


def patch_setting(name: str, value: int):
    from unittest.mock import patch

    return patch.object(settings, name, value)


# --- Picklable wrappers (pure, no pool) ---------------------------------------


def test_render_thumbnail_bytes_returns_webp_and_dims() -> None:
    out, w, h = render_thumbnail_bytes(_png_bytes(800, 600), 320)
    assert isinstance(out, bytes)
    assert (w, h) == (320, 240)  # aspect ratio preserved
    assert Image.open(io.BytesIO(out)).format == "WEBP"


def test_render_preview_bytes_returns_webp_and_dims() -> None:
    out, w, h = render_preview_bytes(_png_bytes(1600, 900), 1280, None, "abcdef12")
    assert isinstance(out, bytes)
    assert (w, h) == (1280, 720)  # 16:9 aspect ratio preserved
    assert Image.open(io.BytesIO(out)).format == "WEBP"


# --- Pool roundtrip (spawn + pickling) ----------------------------------------


async def test_run_in_pool_executes_in_worker() -> None:
    assert await run_in_pool(add, 2, 3) == 5


async def test_run_in_pool_renders_image_in_worker() -> None:
    out, w, h = await run_in_pool(render_thumbnail_bytes, _png_bytes(800, 600), 320)
    assert (w, h) == (320, 240)
    assert Image.open(io.BytesIO(out)).format == "WEBP"


# --- Hard timeout -------------------------------------------------------------


async def test_run_in_pool_hard_timeout_kills_hung_task() -> None:
    # A 30s task under a 1s timeout must be killed and surface TimeoutError fast.
    with patch_setting("image_processing_timeout_seconds", 1):
        with pytest.raises(TimeoutError):
            await run_in_pool(sleep_seconds, 30)


async def test_run_in_pool_no_timeout_when_disabled() -> None:
    # 0 disables the timeout; a quick task still completes normally.
    with patch_setting("image_processing_timeout_seconds", 0):
        assert await run_in_pool(add, 1, 1) == 2


# --- Pixel cap re-applied in spawn workers ------------------------------------


async def test_pixel_cap_applied_inside_worker() -> None:
    cap = await run_in_pool(get_max_image_pixels)
    assert cap == (settings.max_image_pixels or None)
