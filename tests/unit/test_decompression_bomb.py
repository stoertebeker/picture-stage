"""Unit tests for the decompression-bomb guard (picture-stage-ccx).

`app/images/processing.py` caps the decoded pixel count module-wide via
``Image.MAX_IMAGE_PIXELS`` and promotes Pillow's ``DecompressionBombWarning`` to
an error, so the cap bites deterministically at the configured pixel count
instead of only at twice that value. Normal photo-sized images stay unaffected.
"""

import io
import warnings

import pytest
from PIL import Image

from app.config import settings
from app.images.processing import generate_thumbnail


def _solid_png(width: int, height: int) -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_max_image_pixels_cap_applied_on_import() -> None:
    # Importing the processing module installs the configured cap module-wide.
    assert Image.MAX_IMAGE_PIXELS == settings.max_image_pixels


def test_oversized_image_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lower the cap so a small, cheap image already exceeds it. 120x120 = 14_400 px
    # sits between the cap (10_000) and twice the cap (20_000): Pillow would only
    # warn, but our promoted filter turns that into a raised exception — so no
    # multi-hundred-MB allocation happens.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10_000)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with pytest.raises((Image.DecompressionBombError, Image.DecompressionBombWarning)):
            generate_thumbnail(_solid_png(120, 120), 320)


def test_normal_image_processed() -> None:
    # A real-world-sized photo stays far below the default 100 MP cap and
    # processes normally (no exception, correctly downscaled).
    _buf, width, height = generate_thumbnail(_solid_png(1400, 900), 320)
    assert width == 320
    assert height == int(900 * 320 / 1400)
