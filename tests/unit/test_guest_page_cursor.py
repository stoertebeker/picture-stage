"""Opaque image cursors reject malformed types and mismatched query contexts."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.guest.service import _decode_page_cursor, _encode_page_cursor


@pytest.mark.parametrize(
    "sort_by,keys",
    [
        (ImageSortBy.sort_order, [False, str(uuid.uuid4())]),
        (ImageSortBy.sort_order, [2**40, str(uuid.uuid4())]),
        (ImageSortBy.sort_order, [1, "not-a-uuid"]),
        (ImageSortBy.sort_order, [1]),
        (ImageSortBy.filename, ["x" * 513, str(uuid.uuid4())]),
        (ImageSortBy.filename, [1, str(uuid.uuid4())]),
        (ImageSortBy.exif_date, ["2025-02-30T00:00:00", 0, str(uuid.uuid4())]),
        (ImageSortBy.exif_date, ["2025-01-01T00:00:00", False, str(uuid.uuid4())]),
        (ImageSortBy.exif_date, ["2025-01-01T00:00:00+00:00", 0, str(uuid.uuid4())]),
    ],
)
def test_signed_cursor_still_validates_key_types(sort_by, keys):
    gallery = SimpleNamespace(id=uuid.uuid4())
    cursor = _encode_page_cursor(gallery, sort_by, SortDirection.asc, ImageFilter.all, keys)
    with pytest.raises(HTTPException) as exc:
        _decode_page_cursor(cursor, gallery, sort_by, SortDirection.asc, ImageFilter.all)
    assert exc.value.status_code == 422


@pytest.mark.parametrize(
    "sort_by,keys",
    [
        (ImageSortBy.sort_order, [-(2**31), str(uuid.uuid4())]),
        (ImageSortBy.filename, ["é name ' \" <>.jpg", str(uuid.uuid4())]),
        (ImageSortBy.exif_date, ["9999-12-31T23:59:59", 2**31 - 1, str(uuid.uuid4())]),
        (ImageSortBy.exif_date, ["0001-01-01T00:00:00", -(2**31), str(uuid.uuid4())]),
    ],
)
def test_cursor_roundtrip_and_query_binding(sort_by, keys):
    gallery = SimpleNamespace(id=uuid.uuid4())
    cursor = _encode_page_cursor(gallery, sort_by, SortDirection.desc, ImageFilter.unrated, keys)
    decoded = _decode_page_cursor(cursor, gallery, sort_by, SortDirection.desc, ImageFilter.unrated)
    assert decoded[-1] == uuid.UUID(keys[-1])
    for different in (
        (SimpleNamespace(id=uuid.uuid4()), sort_by, SortDirection.desc, ImageFilter.unrated),
        (gallery, sort_by, SortDirection.asc, ImageFilter.unrated),
        (gallery, sort_by, SortDirection.desc, ImageFilter.all),
    ):
        with pytest.raises(HTTPException) as exc:
            _decode_page_cursor(cursor, *different)
        assert exc.value.status_code == 422


@pytest.mark.parametrize("character", ["a", "é", "界", "🙂", "\x01"])
@pytest.mark.parametrize("sort_dir", list(SortDirection))
@pytest.mark.parametrize("filter_mode", list(ImageFilter))
def test_maximum_unicode_filename_cursor_fits_bound(character, sort_dir, filter_mode):
    from app.guest.service import MAX_CURSOR_LENGTH

    gallery = SimpleNamespace(id=uuid.uuid4())
    filename = character * 512
    image_id = uuid.uuid4()
    cursor = _encode_page_cursor(gallery, ImageSortBy.filename, sort_dir, filter_mode, [filename, str(image_id)])
    assert len(cursor) <= MAX_CURSOR_LENGTH
    assert _decode_page_cursor(cursor, gallery, ImageSortBy.filename, sort_dir, filter_mode) == [filename, image_id]


def test_oversize_cursor_is_rejected_before_decode():
    from app.guest.service import MAX_CURSOR_LENGTH

    gallery = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        _decode_page_cursor(
            "a" * (MAX_CURSOR_LENGTH + 1), gallery, ImageSortBy.filename, SortDirection.asc, ImageFilter.all
        )
    assert exc.value.status_code == 422


def test_worst_accepted_filename_representation_has_finite_cursor_bound():
    from app.guest.service import MAX_CURSOR_LENGTH

    gallery = SimpleNamespace(id=uuid.uuid4())
    filename = "\x01" * 512  # PostgreSQL text accepts this; JSON uses six bytes per character.
    image_id = uuid.uuid4()
    cursor = _encode_page_cursor(
        gallery, ImageSortBy.filename, SortDirection.desc, ImageFilter.favorited, [filename, str(image_id)]
    )
    # 3072 filename bytes +119 fixed metadata bytes, URL-base64 without padding,
    # a dot and64 HMAC hex characters. UTF-8 characters require at most4 bytes.
    assert len(cursor) == 4320 < MAX_CURSOR_LENGTH
    assert _decode_page_cursor(cursor, gallery, ImageSortBy.filename, SortDirection.desc, ImageFilter.favorited) == [
        filename,
        image_id,
    ]
