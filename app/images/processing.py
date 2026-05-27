import hashlib
import io
from typing import BinaryIO

from PIL import Image, ImageDraw, ImageFont

from app.config import settings

PREVIEW_SIZES = {
    "thumb_sm": 320,
    "thumb_md": 640,
    "preview": 1280,
}


def generate_thumbnail(image_data: BinaryIO, max_width: int) -> tuple[io.BytesIO, int, int]:
    img = Image.open(image_data)
    img = img.convert("RGB")

    ratio = max_width / img.width
    if ratio < 1:
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    buf.seek(0)
    return buf, img.width, img.height


def apply_watermark(image_data: BinaryIO, text: str | None = None) -> io.BytesIO:
    img = Image.open(image_data).convert("RGBA")

    wm_text = text or settings.watermark_text
    opacity = settings.watermark_opacity
    font_size = max(16, int(img.width * settings.watermark_font_size_ratio))

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)

    bbox = draw.textbbox((0, 0), wm_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = img.width - text_width - 20
    y = img.height - text_height - 20

    draw.text((x, y), wm_text, fill=(255, 255, 255, opacity), font=font)

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    composited.save(buf, format="WEBP", quality=85)
    buf.seek(0)
    return buf


def generate_preview_with_watermark(
    image_data: BinaryIO, max_width: int, watermark_text: str
) -> tuple[io.BytesIO, int, int]:
    img = Image.open(image_data).convert("RGBA")

    ratio = max_width / img.width
    if ratio < 1:
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)

    font_size = max(16, int(img.width * settings.watermark_font_size_ratio))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)

    bbox = draw.textbbox((0, 0), watermark_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = img.width - text_width - 20
    y = img.height - text_height - 20

    draw.text((x, y), watermark_text, fill=(255, 255, 255, settings.watermark_opacity), font=font)

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    composited.save(buf, format="WEBP", quality=85)
    buf.seek(0)
    return buf, composited.width, composited.height


def extract_exif(image_data: BinaryIO) -> dict:
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
