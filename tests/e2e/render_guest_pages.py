"""Render real guest templates to stdout for the disposable browser fixture."""

import json
import uuid
from types import SimpleNamespace

from starlette.requests import Request

from app.db.models import PreviewVariant
from app.frontend.deps import templates
from app.frontend.guest import _page_context
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.guest.service import GuestImagePage
from app.i18n import t
from app.selections.schemas import SelectionState

images = [
    SimpleNamespace(
        id=uuid.UUID(int=index + 1),
        filename=f"{index + 1}.jpg",
        sort_order=index,
        previews=[
            SimpleNamespace(variant=variant, storage_key=f"page/{index + 1}/{variant.value}")
            for variant in PreviewVariant
        ],
    )
    for index in range(61)
]
selections = {
    image.id: SelectionState(image_id=image.id, selected=index % 2 == 0, favorited=False, comment=None)
    for index, image in enumerate(images)
}
request = Request({"type": "http", "headers": [], "root_path": "/mount"})


def render(rows, sort_by, sort_dir, filter_mode, start, cursor, *, initial=False):
    page_rows = rows[start : start + 30]
    page = GuestImagePage(
        page_rows, {image.id: selections[image.id] for image in page_rows}, cursor, 61, 31, 0, len(rows)
    )
    context = _page_context(page, sort_by, sort_dir, filter_mode, append_mode=start > 0)
    context.update(
        request=request,
        token="fixture",
        guest_csrf_token="csrf",
        gallery_name="61 images",
        gallery_message=None,
        requires_password=False,
        session_completed=False,
        t=lambda key, **kw: t(key, "en", **kw),
        locale="en",
    )
    return templates.env.get_template("guest/viewer.html" if initial else "guest/_image_grid.html").render(**context)


responses = {}
initial = None
for sort_by in ImageSortBy:
    for sort_dir in SortDirection:
        for filter_mode in ImageFilter:
            rows = [
                image
                for image in images
                if filter_mode == ImageFilter.all
                or (filter_mode == ImageFilter.selected and selections[image.id].selected)
                or (filter_mode == ImageFilter.unrated and not selections[image.id].selected)
            ]
            rows.sort(
                key=lambda image: (image.filename if sort_by == ImageSortBy.filename else image.sort_order, image.id),
                reverse=sort_dir == SortDirection.desc,
            )
            for start in range(0, max(1, len(rows)), 30):
                cursor = f"{sort_by}:{sort_dir}:{filter_mode}:{start + 30}" if start + 30 < len(rows) else None
                current = f"{sort_by}:{sort_dir}:{filter_mode}:{start}" if start else ""
                key = "|".join([sort_by, sort_dir, filter_mode, current])
                responses[key] = render(rows, sort_by, sort_dir, filter_mode, start, cursor)
                if (
                    start == 0
                    and sort_by == ImageSortBy.sort_order
                    and sort_dir == SortDirection.asc
                    and filter_mode == ImageFilter.all
                ):
                    initial = render(rows, sort_by, sort_dir, filter_mode, start, cursor, initial=True)
print(json.dumps({"initial": initial, "responses": responses}))
