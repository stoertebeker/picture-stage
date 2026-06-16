import hashlib
import io
import warnings
from typing import Any, BinaryIO

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from app.config import settings

# Decompression-bomb guard (picture-stage-ccx). Cap the decoded pixel count
# module-wide so every Image.open()/convert()/resize() path is covered. Pillow
# only *warns* at MAX_IMAGE_PIXELS and raises DecompressionBombError at twice
# that value, so we additionally promote the warning to an error to make the cap
# deterministic at exactly the configured pixel count.
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels or None
if settings.max_image_pixels:
    warnings.simplefilter("error", Image.DecompressionBombWarning)

PREVIEW_SIZES = {
    "thumb_sm": 320,
    "thumb_md": 640,
    "preview": 1280,
}

VALID_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}

MARGIN = 20


def _resolve_watermark_settings(
    watermark_config: dict[str, Any] | None,
    gallery_id: str | None,
    img_width: int,
) -> tuple[str, str, int, int]:
    """Resolve per-gallery watermark settings with global fallback.

    Returns (text, position, opacity_alpha, font_size).
    """
    cfg = watermark_config or {}

    # Text: per-gallery > global default; resolve {gallery_id} placeholder
    text = cfg.get("text") or settings.watermark_text
    if gallery_id:
        text = text.replace("{gallery_id}", gallery_id[:8].upper())

    # Position: per-gallery > global default
    position = cfg.get("position") or settings.watermark_position
    if position not in VALID_POSITIONS:
        position = "bottom-right"

    # Opacity: per-gallery (0.0-1.0) > global default (0.0-1.0) -> convert to alpha (0-255)
    opacity_raw = cfg.get("opacity")
    if opacity_raw is not None:
        opacity_float = max(0.0, min(1.0, float(opacity_raw)))
    else:
        opacity_float = max(0.0, min(1.0, settings.watermark_opacity))
    opacity_alpha = int(opacity_float * 255)

    # Font size: per-gallery absolute > global absolute > ratio-based fallback
    font_size_raw = cfg.get("font_size")
    if font_size_raw is not None:
        font_size = max(10, min(200, int(font_size_raw)))
    elif settings.watermark_font_size:
        font_size = max(10, min(200, settings.watermark_font_size))
    else:
        font_size = max(16, int(img_width * settings.watermark_font_size_ratio))

    return text, position, opacity_alpha, font_size


def _calculate_text_position(
    position: str,
    img_width: int,
    img_height: int,
    text_width: int,
    text_height: int,
) -> tuple[int, int]:
    """Calculate (x, y) for the watermark text based on named position."""
    if position == "top-left":
        return MARGIN, MARGIN
    if position == "top-right":
        return img_width - text_width - MARGIN, MARGIN
    if position == "bottom-left":
        return MARGIN, img_height - text_height - MARGIN
    if position == "center":
        return (img_width - text_width) // 2, (img_height - text_height) // 2
    # bottom-right (default)
    return img_width - text_width - MARGIN, img_height - text_height - MARGIN


def generate_thumbnail(image_data: BinaryIO, max_width: int) -> tuple[io.BytesIO, int, int]:
    img = Image.open(image_data).convert("RGB")

    ratio = max_width / img.width
    if ratio < 1:
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    buf.seek(0)
    return buf, img.width, img.height


def apply_watermark(
    image_data: BinaryIO,
    text: str | None = None,
    *,
    watermark_config: dict[str, Any] | None = None,
    gallery_id: str | None = None,
) -> io.BytesIO:
    img = Image.open(image_data).convert("RGBA")

    wm_text, position, opacity_alpha, font_size = _resolve_watermark_settings(watermark_config, gallery_id, img.width)
    # Legacy: explicit text parameter overrides config
    if text:
        wm_text = text

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font: FreeTypeFont
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)  # type: ignore[assignment]

    bbox = draw.textbbox((0, 0), wm_text, font=font)
    text_width = int(bbox[2] - bbox[0])
    text_height = int(bbox[3] - bbox[1])
    x, y = _calculate_text_position(position, img.width, img.height, text_width, text_height)

    draw.text((x, y), wm_text, fill=(255, 255, 255, opacity_alpha), font=font)

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    composited.save(buf, format="WEBP", quality=85)
    buf.seek(0)
    return buf


def generate_preview_with_watermark(
    image_data: BinaryIO,
    max_width: int,
    watermark_text: str,
    *,
    watermark_config: dict[str, Any] | None = None,
    gallery_id: str | None = None,
) -> tuple[io.BytesIO, int, int]:
    img = Image.open(image_data).convert("RGBA")

    ratio = max_width / img.width
    if ratio < 1:
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

    # Watermark explicitly disabled for this gallery: emit the resized preview
    # without any overlay (NULL/true keep the watermark on).
    if (watermark_config or {}).get("enabled") is False:
        plain = img.convert("RGB")
        plain_buf = io.BytesIO()
        plain.save(plain_buf, format="WEBP", quality=85)
        plain_buf.seek(0)
        return plain_buf, plain.width, plain.height

    wm_text, position, opacity_alpha, font_size = _resolve_watermark_settings(watermark_config, gallery_id, img.width)
    # Legacy: explicit watermark_text parameter overrides config-resolved text
    if watermark_text:
        wm_text = watermark_text

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font2: FreeTypeFont
    try:
        font2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=font_size)
    except OSError:
        font2 = ImageFont.load_default(size=font_size)  # type: ignore[assignment]

    bbox = draw.textbbox((0, 0), wm_text, font=font2)
    text_width = int(bbox[2] - bbox[0])
    text_height = int(bbox[3] - bbox[1])
    x, y = _calculate_text_position(position, img.width, img.height, text_width, text_height)

    draw.text((x, y), wm_text, fill=(255, 255, 255, opacity_alpha), font=font2)

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    composited.save(buf, format="WEBP", quality=85)
    buf.seek(0)
    return buf, composited.width, composited.height


def extract_exif(image_data: BinaryIO) -> dict[str, str]:
    try:
        img = Image.open(image_data)
        exif_data = img.getexif()
        if not exif_data:
            return {}
        safe_exif = {}
        for tag_id, value in exif_data.items():
            try:
                str(value)
                safe_exif[str(tag_id)] = str(value)
            except Exception:  # noqa: S112
                continue
        return safe_exif
    except Exception:
        return {}


def compute_sha256(data: BinaryIO) -> str:
    sha = hashlib.sha256()
    data.seek(0)
    while chunk := data.read(65536):
        sha.update(chunk)
    data.seek(0)
    return sha.hexdigest()


def get_image_dimensions(image_data: BinaryIO) -> tuple[int, int]:
    img = Image.open(image_data)
    image_data.seek(0)
    return img.width, img.height
