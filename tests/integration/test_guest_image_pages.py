"""Guest pages bound hydration/signing independently of gallery/history size."""

import html
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, func, select

from app.auth.passwords import hash_token
from app.db.models import (
    CurrentSelection,
    Gallery,
    GalleryStatus,
    Image,
    ImagePreview,
    PreviewVariant,
    SelectionAction,
    SelectionEvent,
    ShareSession,
)
from app.guest.schemas import ImageFilter, ImageSortBy, SortDirection
from app.guest.service import MAX_CURSOR_LENGTH, load_guest_image_page
from app.images.capture_date import MISSING_CAPTURE_DATE, parse_exif_date
from app.selections.service import get_current_selections


def page_metadata(response):
    match = re.search(r"data-guest-page='([^']*)'", response.text)
    assert match is not None
    return json.loads(html.unescape(match[1]))


async def gallery_fixture(db, owner, count=67):
    token = "bounded-gallery-" + uuid.uuid4().hex
    digest, salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id, name="Paged", status=GalleryStatus.shared, share_token_hash=digest, share_token_salt=salt
    )
    db.add(gallery)
    await db.flush()
    session = ShareSession(gallery_id=gallery.id)
    db.add(session)
    images = [
        Image(
            gallery_id=gallery.id,
            filename=f"{index // 3:03}.jpg",
            storage_key=f"original/{index}",
            sort_order=index // 2,
            exif={"DateTimeOriginal": "2025:01:01 01:02:03"} if index % 4 else None,
        )
        for index in range(count)
    ]
    db.add_all(images)
    await db.flush()
    db.add_all(
        [
            ImagePreview(
                image_id=image.id, variant=variant, storage_key=f"{image.id}/{variant.value}", width=10, height=10
            )
            for image in images
            for variant in PreviewVariant
        ]
    )
    db.add_all(
        [
            SelectionEvent(image_id=image.id, share_session_id=session.id, action=action)
            for index, image in enumerate(images)
            for action in ([SelectionAction.select] if index % 3 == 0 else [])
            + ([SelectionAction.favorite] if index % 5 == 0 else [])
        ]
    )
    await db.commit()
    return token, gallery, session, images


async def test_html_fragments_json_bound_hydration_and_signing(client, db, owner, monkeypatch):
    token, _gallery, session, images = await gallery_fixture(db, owner)
    # A long retained history must never enter a page request's ORM identity map.
    db.add_all(
        [
            SelectionEvent(image_id=images[0].id, share_session_id=session.id, action=SelectionAction.select)
            for _ in range(240)
        ]
    )
    await db.commit()
    signed = []
    hydrated = []
    hydrated_events = []
    from app.guest import service

    real_sign = service.sign_url

    def sign(path, expires_in):
        signed.append(path)
        return real_sign(path, expires_in)

    def loaded(image, context):
        hydrated.append(image.id)

    def event_loaded(row, context):
        hydrated_events.append(row.id)

    monkeypatch.setattr(service, "sign_url", sign)
    event.listen(Image, "load", loaded)
    event.listen(SelectionEvent, "load", event_loaded)
    try:
        initial = await client.get(f"/g/{token}", headers={"Accept": "text/html"})
        assert initial.status_code == 200
        first = page_metadata(initial)
        assert len(first["images"]) == 30
        assert first["total_images"] == len(images)
        assert len(hydrated) == 30 and len(signed) == 90
        assert not hydrated_events
        cursor = first["next_cursor"]
        all_ids = [row["id"] for row in first["images"]]
        for expected_size in (30, 7):
            signed.clear()
            hydrated.clear()
            response = await client.get(f"/g/{token}/gallery-images", params={"cursor": cursor})
            assert response.status_code == 200
            page = page_metadata(response)
            assert page["append"] is True
            assert len(page["images"]) == expected_size
            assert len(hydrated) == expected_size and len(signed) == expected_size * 3
            assert not hydrated_events
            # Continuation stays flat: no nested grid wrapper.
            assert "grid grid-cols-2" not in response.text
            all_ids.extend(row["id"] for row in page["images"])
            cursor = page["next_cursor"]
        assert cursor is None and len(set(all_ids)) == len(images)
        for cursor in (None, first["next_cursor"]):
            signed.clear()
            hydrated.clear()
            response = await client.get(f"/g/{token}/images", params={"cursor": cursor} if cursor else {})
            assert response.status_code == 200
            assert len(response.json()) == 30
            assert len(hydrated) == 30 and len(signed) == 90
            assert response.headers["X-Next-Cursor"]
            assert not hydrated_events
    finally:
        event.remove(Image, "load", loaded)
        event.remove(SelectionEvent, "load", event_loaded)


@pytest.mark.parametrize("sort_by", list(ImageSortBy))
@pytest.mark.parametrize("sort_dir", list(SortDirection))
@pytest.mark.parametrize("filter_mode", list(ImageFilter))
async def test_cursor_pages_match_all_filters_and_sorts(db, owner, sort_by, sort_dir, filter_mode):
    _token, gallery, _session, images = await gallery_fixture(db, owner, 73)
    # Edges: actual dates after the historic missing sentinel stay after it in
    # ascending order; malformed truthy originals block DateTime fallback.
    images[0].exif = {"DateTimeOriginal": "9999:12:31 23:59:59"}
    images[1].exif = {"DateTimeOriginal": "bad", "DateTime": "2020:01:01 00:00:00"}
    images[2].exif = {"DateTimeOriginal": "", "DateTime": "2020:01:01 00:00:00"}
    images[3].exif = {"DateTimeOriginal": "2025:02:30 00:00:00"}
    await db.commit()
    selections = {row.image_id: row for row in await get_current_selections(gallery.id, db)}
    expected = [
        image
        for image in images
        if filter_mode == ImageFilter.all
        or (filter_mode == ImageFilter.selected and selections[image.id].selected)
        or (filter_mode == ImageFilter.favorited and selections[image.id].favorited)
        or (
            filter_mode == ImageFilter.unrated
            and not selections[image.id].selected
            and not selections[image.id].favorited
        )
    ]

    def key(image):
        if sort_by == ImageSortBy.exif_date:
            return (parse_exif_date(image.exif) or MISSING_CAPTURE_DATE, image.sort_order, image.id)
        return (image.filename if sort_by == ImageSortBy.filename else image.sort_order, image.id)

    expected.sort(key=key, reverse=sort_dir == SortDirection.desc)
    ids, cursor = [], None
    while True:
        page = await load_guest_image_page(gallery, db, sort_by, sort_dir, filter_mode, cursor)
        assert len(page.images) <= 30 and len(page.selections) <= 30
        assert page.filtered_count == len(expected)
        ids.extend(image.id for image in page.images)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert ids == [image.id for image in expected]


async def test_cursor_validation_and_access_gate(client, db, owner):
    token, gallery, _session, _images = await gallery_fixture(db, owner)
    first = await client.get(f"/g/{token}/images")
    assert first.status_code == 200
    cursor = first.headers["X-Next-Cursor"]
    for endpoint in ("images", "gallery-images"):
        for params in (
            {"offset": 10**12},
            {"cursor": "broken"},
            {"cursor": cursor + "0"},
            {"cursor": cursor, "sort_dir": "desc"},
            {"cursor": cursor, "filter": "selected"},
            {"cursor": cursor, "sort_by": "filename"},
            {"cursor": "a" * (MAX_CURSOR_LENGTH + 1)},
        ):
            response = await client.get(f"/g/{token}/{endpoint}", params=params)
            assert response.status_code == 422
    token2, _other, _other_session, _ = await gallery_fixture(db, owner, 1)
    assert (await client.get(f"/g/{token2}/images", params={"cursor": cursor})).status_code == 422
    # Continuations retain exactly the same password/session fence.
    from app.auth.passwords import hash_password

    gallery.password_hash = hash_password("password")
    gallery.authorization_epoch += 1
    await db.commit()
    for endpoint in ("images", "gallery-images"):
        assert (await client.get(f"/g/{token}/{endpoint}", params={"cursor": cursor})).status_code == 403


async def test_projection_versions_dimensions_rollback_and_cascade(db, verify_db, owner):
    _token, gallery, session, images = await gallery_fixture(db, owner, 1)
    image = images[0]
    image_id, gallery_id, session_id = image.id, gallery.id, session.id
    now = datetime.now(UTC)

    async def append(action, timestamp, comment=None):
        row = SelectionEvent(
            image_id=image_id, share_session_id=session_id, action=action, created_at=timestamp, comment=comment
        )
        db.add(row)
        await db.flush()
        return row

    await append(SelectionAction.deselect, now)
    await append(SelectionAction.select, now - timedelta(seconds=1))
    await append(SelectionAction.unfavorite, now - timedelta(seconds=2))
    await append(SelectionAction.favorite, now)
    await append(SelectionAction.comment, now, "")
    await append(SelectionAction.comment, now + timedelta(seconds=1), None)
    await db.commit()
    [state] = await get_current_selections(gallery_id, verify_db)
    assert state.selected is False and state.favorited is True and state.comment == ""
    from app.galleries.export import _get_materialized_selections

    [exported] = await _get_materialized_selections(gallery_id, verify_db)
    assert exported["selected"] == state.selected
    assert exported["favorited"] == state.favorited
    assert exported["comment"] == state.comment
    # A deterministic ID breaks equal timestamp ties, independently per field.
    await append(SelectionAction.select, now)
    await db.commit()
    [state] = await get_current_selections(gallery_id, verify_db)
    assert state.selected is True
    await append(SelectionAction.deselect, now + timedelta(seconds=3))
    await db.rollback()
    [state] = await get_current_selections(gallery_id, verify_db)
    assert state.selected is True
    await verify_db.rollback()
    await verify_db.execute(delete(Image).where(Image.id == image_id))
    await verify_db.commit()
    assert await verify_db.scalar(select(func.count()).select_from(CurrentSelection)) == 0
    assert await verify_db.scalar(select(func.count()).select_from(SelectionEvent)) == 0


@pytest.mark.parametrize("character", ["界", "🙂", "\x01"])
@pytest.mark.parametrize("sort_dir", list(SortDirection))
@pytest.mark.parametrize("surface", ["images", "gallery-images"])
async def test_maximum_unicode_filename_continues_on_json_and_html(client, db, owner, character, sort_dir, surface):
    token, _gallery, _session, images = await gallery_fixture(db, owner, 61)
    filename = character * 512
    for image in images:
        image.filename = filename
    await db.commit()
    params = {"sort_by": "filename", "sort_dir": sort_dir.value, "filter": "all"}
    received = []
    for expected_count in (30, 30, 1):
        response = await client.get(f"/g/{token}/{surface}", params=params)
        assert response.status_code == 200
        if surface == "images":
            rows = response.json()
            cursor = response.headers["X-Next-Cursor"]
        else:
            page = page_metadata(response)
            rows, cursor = page["images"], page["next_cursor"]
        assert len(rows) == expected_count
        assert all(row["filename"] == filename for row in rows)
        received.extend(row["id"] for row in rows)
        if cursor:
            assert len(cursor) <= MAX_CURSOR_LENGTH
            params["cursor"] = cursor
    assert not cursor
    expected = sorted((image.id for image in images), reverse=sort_dir == SortDirection.desc)
    assert received == [str(image_id) for image_id in expected]
