"""Shared guest-facing enums.

Single source of truth for the sort/filter query enums used by both guest
routers (the JSON API ``app/guest/router.py`` and the HTML viewer
``app/frontend/guest.py``) — previously duplicated in both (picture-stage-d7z).
"""

import enum


class ImageSortBy(enum.StrEnum):
    sort_order = "sort_order"
    filename = "filename"
    exif_date = "exif_date"


class SortDirection(enum.StrEnum):
    asc = "asc"
    desc = "desc"


class ImageFilter(enum.StrEnum):
    all = "all"
    selected = "selected"
    favorited = "favorited"
    unrated = "unrated"
