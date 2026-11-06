"""Image decoders must be selected by content, with a strict supported set."""

import base64
import hashlib
import io
from unittest.mock import patch

import pytest
from PIL import Image, Jpeg2KImagePlugin, UnidentifiedImageError

from app.images.processing import (
    InvalidImageContentError,
    extract_exif,
    extract_image_metadata,
    generate_preview_with_watermark,
    generate_thumbnail,
    get_image_dimensions,
    render_preview_bytes,
    render_thumbnail_bytes,
)

# Harmless 4x4 JPEG2000 image. Embedding it avoids depending on the local
# Pillow build having JPEG2000 encoding enabled merely to run this test.
_JP2 = base64.b64decode(
    "AAAADGpQICANCocKAAAAFGZ0eXBqcDIgAAAAAGpwMiAAAAAtanAyaAAAABZpaGRyAAAABAAAAAQAAwcHAAAAAAAPY29scgEAAAAAABAA"
    "AACeanAyY/9P/1EALwAAAAAABAAAAAQAAAAAAAAAAAAAAAQAAAAEAAAAAAAAAAAAAwcBAQcBAQcBAf9SAAwAAAABAAIEBAAB/1wACkBA"
    "SEhQSEhQ/2QAJQABQ3JlYXRlZCBieSBPcGVuSlBFRyB2ZXJzaW9uIDIuNS40/5AACgAAAAAAIAAB/5PPtAQA34AIB9+ACAeAgICAgID/2Q=="
)
_J2K = base64.b64decode(
    "/0//UQAvAAAAAAAEAAAABAAAAAAAAAAAAAAABAAAAAQAAAAAAAAAAAADBwEBBwEBBwEB/1IADAAAAAEAAgQEAAH/XAAKQEBISFBISFD/"
    "ZAAlAAFDcmVhdGVkIGJ5IE9wZW5KUEVHIHZlcnNpb24gMi41LjT/kAAKAAAAAAAgAAH/k8+0BADfgAgH34AIB4CAgICAgP/Z"
)


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_supported_images_retain_metadata_and_preview_behavior(image_format: str) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(buf, format=image_format)
    data = buf.getvalue()
    digest, width, height, _exif = extract_image_metadata(data)
    assert (digest, width, height) == (hashlib.sha256(data).hexdigest(), 32, 24)
    for rendered in (render_thumbnail_bytes(data, 20), render_preview_bytes(data, 20, {"enabled": False}, None)):
        output, width, height = rendered
        assert (width, height) == (20, 15)
        assert Image.open(io.BytesIO(output)).format == "WEBP"


@pytest.mark.parametrize(
    "entrypoint",
    [
        lambda data: extract_image_metadata(data),
        lambda data: get_image_dimensions(io.BytesIO(data)),
        lambda data: generate_thumbnail(io.BytesIO(data), 20),
        lambda data: generate_preview_with_watermark(io.BytesIO(data), 20, ""),
        lambda data: render_preview_bytes(data, 20, None, None),
    ],
)
@pytest.mark.parametrize("data", [_JP2, _J2K], ids=["jp2-container", "raw-codestream"])
def test_jpeg2000_plugin_never_runs_for_upload_and_preview_entrypoints(entrypoint, data: bytes) -> None:
    with patch.object(Jpeg2KImagePlugin.Jpeg2KImageFile, "_open", side_effect=AssertionError("JPEG2000 invoked")) as op:
        with pytest.raises(UnidentifiedImageError):
            entrypoint(data)
        op.assert_not_called()


def test_exif_does_not_invoke_jpeg2000_plugin() -> None:
    with patch.object(Jpeg2KImagePlugin.Jpeg2KImageFile, "_open", side_effect=AssertionError("JPEG2000 invoked")) as op:
        assert extract_exif(io.BytesIO(_JP2)) == {}
        op.assert_not_called()


def test_other_unsupported_format_does_not_enter_preview() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="BMP")
    with pytest.raises(UnidentifiedImageError):
        render_thumbnail_bytes(buf.getvalue(), 20)


def test_truncated_image_is_detected_after_a_valid_header() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (128, 128), "red").save(buf, format="PNG")
    truncated = buf.getvalue()[:41]
    assert Image.open(io.BytesIO(truncated)).size == (128, 128)
    with pytest.raises(InvalidImageContentError):
        extract_image_metadata(truncated)
