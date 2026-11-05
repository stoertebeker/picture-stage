"""Persist the guest capture-date ordering key at metadata write time."""

from datetime import datetime
from typing import Any

MISSING_CAPTURE_DATE = datetime(9999, 1, 1)


def parse_exif_date(exif: dict[str, Any] | None) -> datetime | None:
    if not exif:
        return None
    raw = exif.get("DateTimeOriginal") or exif.get("DateTime")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
