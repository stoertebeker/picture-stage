"""Unit tests for per-gallery watermark text + enable/disable (picture-stage-bsr).

The overlay is verified structurally: render onto a solid black image and read
back the brightest pixel. White watermark text raises the maximum luminance;
with the watermark disabled the preview stays dark.
"""

import io

import pytest
from PIL import Image
from pydantic import ValidationError

from app.galleries.schemas import WatermarkConfig
from app.images.processing import _resolve_watermark_settings, generate_preview_with_watermark


def _solid_black_png(width: int = 1400, height: int = 900) -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 0, 0)).save(buf, format="PNG")
    buf.seek(0)
    return buf


def _max_luminance(webp_buf: io.BytesIO) -> int:
    # getextrema() returns (min, max) for the single "L" band — no per-pixel iteration.
    return Image.open(webp_buf).convert("L").getextrema()[1]


def test_watermark_enabled_draws_overlay() -> None:
    buf, _, _ = generate_preview_with_watermark(
        _solid_black_png(), 1280, "", watermark_config={"text": "WM"}, gallery_id="abcd1234"
    )
    assert _max_luminance(buf) > 100  # white-ish text pixels present


def test_watermark_disabled_skips_overlay() -> None:
    buf, _, _ = generate_preview_with_watermark(
        _solid_black_png(), 1280, "", watermark_config={"enabled": False, "text": "WM"}, gallery_id="abcd1234"
    )
    assert _max_luminance(buf) < 30  # stays dark: no overlay rendered


def test_watermark_enabled_none_defaults_on() -> None:
    """No config at all -> watermark stays on (global default)."""
    buf, _, _ = generate_preview_with_watermark(
        _solid_black_png(), 1280, "", watermark_config=None, gallery_id="abcd1234"
    )
    assert _max_luminance(buf) > 100


def test_watermark_enabled_true_explicit_draws_overlay() -> None:
    buf, _, _ = generate_preview_with_watermark(
        _solid_black_png(), 1280, "", watermark_config={"enabled": True, "text": "WM"}, gallery_id="abcd1234"
    )
    assert _max_luminance(buf) > 100


def test_resolve_uses_custom_text() -> None:
    text, *_ = _resolve_watermark_settings({"text": "Mein Studio"}, "abcd1234", 1280)
    assert text == "Mein Studio"


def test_resolve_resolves_gallery_id_placeholder() -> None:
    text, *_ = _resolve_watermark_settings({"text": "© {gallery_id}"}, "abcd1234ef", 1280)
    assert text == "© ABCD1234"


def test_schema_accepts_enabled_flag() -> None:
    assert WatermarkConfig(enabled=False).model_dump(exclude_none=True) == {"enabled": False}
    assert WatermarkConfig(text="Studio", enabled=True).model_dump(exclude_none=True) == {
        "text": "Studio",
        "enabled": True,
    }


def test_schema_rejects_overlong_text() -> None:
    """The 200-char cap (also enforced server-side) guards the rendered overlay."""
    with pytest.raises(ValidationError):
        WatermarkConfig(text="x" * 201)
