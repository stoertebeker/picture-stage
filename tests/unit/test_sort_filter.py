"""Tests for sort and filter in model view.

Verifies that:
- Sort/filter enums have correct values
- Endpoint accepts sort_by, sort_dir, filter, session_id params
- Filter requires session_id
- EXIF date parsing handles edge cases
- Filters use get_current_selections service
"""

import enum
from datetime import datetime


class TestSortByEnum:
    """Verify ImageSortBy enum values."""

    def test_has_sort_order(self) -> None:
        from app.guest.schemas import ImageSortBy

        assert ImageSortBy.sort_order.value == "sort_order"

    def test_has_filename(self) -> None:
        from app.guest.schemas import ImageSortBy

        assert ImageSortBy.filename.value == "filename"

    def test_has_exif_date(self) -> None:
        from app.guest.schemas import ImageSortBy

        assert ImageSortBy.exif_date.value == "exif_date"

    def test_is_string_enum(self) -> None:
        from app.guest.schemas import ImageSortBy

        assert issubclass(ImageSortBy, str)
        assert issubclass(ImageSortBy, enum.Enum)


class TestSortDirectionEnum:
    """Verify SortDirection enum values."""

    def test_has_asc(self) -> None:
        from app.guest.schemas import SortDirection

        assert SortDirection.asc.value == "asc"

    def test_has_desc(self) -> None:
        from app.guest.schemas import SortDirection

        assert SortDirection.desc.value == "desc"


class TestImageFilterEnum:
    """Verify ImageFilter enum values."""

    def test_has_all(self) -> None:
        from app.guest.schemas import ImageFilter

        assert ImageFilter.all.value == "all"

    def test_has_selected(self) -> None:
        from app.guest.schemas import ImageFilter

        assert ImageFilter.selected.value == "selected"

    def test_has_favorited(self) -> None:
        from app.guest.schemas import ImageFilter

        assert ImageFilter.favorited.value == "favorited"

    def test_has_unrated(self) -> None:
        from app.guest.schemas import ImageFilter

        assert ImageFilter.unrated.value == "unrated"


class TestExifDateParsing:
    """Verify parse_exif_date handles edge cases."""

    def test_valid_exif_date(self) -> None:
        from app.guest.service import parse_exif_date

        result = parse_exif_date({"DateTimeOriginal": "2024:06:15 14:30:00"})
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_datetime_fallback(self) -> None:
        from app.guest.service import parse_exif_date

        result = parse_exif_date({"DateTime": "2024:06:15 14:30:00"})
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_none_exif(self) -> None:
        from app.guest.service import parse_exif_date

        assert parse_exif_date(None) is None

    def test_empty_exif(self) -> None:
        from app.guest.service import parse_exif_date

        assert parse_exif_date({}) is None

    def test_invalid_format(self) -> None:
        from app.guest.service import parse_exif_date

        assert parse_exif_date({"DateTimeOriginal": "not-a-date"}) is None

    def test_non_string_value(self) -> None:
        from app.guest.service import parse_exif_date

        assert parse_exif_date({"DateTimeOriginal": 12345}) is None


class TestEndpointStructure:
    """Verify endpoint accepts correct parameters."""

    def test_endpoint_has_sort_by_param(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "sort_by: ImageSortBy" in source

    def test_endpoint_has_sort_dir_param(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "sort_dir: SortDirection" in source

    def test_endpoint_has_filter_param(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "filter: ImageFilter" in source

    def test_endpoint_has_session_id_param(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        idx = source.index("list_shared_images")
        block = source[idx : idx + 500]
        assert "session_id" in block

    def test_filter_does_not_require_session_id(self) -> None:
        # Selections are gallery-wide (magic-link = one model), so filtering
        # no longer needs a session_id. The old guard must be gone.
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "session_id is required when using filters" not in source

    def test_filter_uses_selections_service(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "get_current_selections" in source

    def test_exif_sort_uses_python_sort(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "parse_exif_date" in source
        assert "images.sort" in source

    def test_defaults_are_sort_order_asc_all(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "ImageSortBy.sort_order" in source
        assert "SortDirection.asc" in source
        assert "ImageFilter.all" in source
