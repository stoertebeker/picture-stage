"""Dashboard query budgets and historical progress contracts on PostgreSQL."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.auth.tokens import create_access_token
from app.db.models import (
    CurrentSelection,
    Gallery,
    Image,
    ImagePreview,
    PendingSignup,
    PreviewVariant,
    SelectionAction,
    SelectionEvent,
    ShareSession,
    UserStatus,
)
from app.frontend.dashboard import _load_gallery_data
from app.galleries.router import get_dashboard


@contextmanager
def _queries(db):
    """Measure handler SQL, excluding authentication and fixture setup."""
    statements = []

    def before_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", before_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)


def _image(gallery, order):
    return Image(
        gallery_id=gallery.id,
        filename=f"image-{order}.jpg",
        storage_key=f"{gallery.id}/{order}/original.jpg",
        sort_order=order,
    )


def _preview(image, variant):
    return ImagePreview(
        image_id=image.id,
        variant=variant,
        storage_key=f"{image.storage_key}/{variant.value}",
        width=640,
        height=480,
    )


@pytest.mark.parametrize("gallery_count", [1, 20, 100])
async def test_dashboard_query_budget_is_constant(db, verify_db, owner, gallery_count):
    galleries = [Gallery(owner_id=owner.id, name=f"Gallery {index}") for index in range(gallery_count)]
    db.add_all(galleries)
    await db.flush()
    images = [_image(gallery, order) for gallery in galleries for order in range(2)]
    sessions = [ShareSession(gallery_id=gallery.id) for gallery in galleries for _ in range(2)]
    db.add_all([*images, *sessions])
    await db.flush()
    for index, image in enumerate(images):
        db.add(_preview(image, PreviewVariant.thumb_md))
        db.add(_preview(image, PreviewVariant.thumb_sm))
        # Repeated actions across sessions must count images, never events.
        for session in sessions[index // 2 * 2 : index // 2 * 2 + 2]:
            for action in (SelectionAction.select, SelectionAction.favorite, SelectionAction.comment):
                db.add(SelectionEvent(image_id=image.id, share_session_id=session.id, action=action))
    await db.commit()

    with _queries(verify_db) as html_queries:
        html = await _load_gallery_data(owner, verify_db)
    assert len(html_queries) == 5
    assert len(html) == gallery_count
    assert all(
        (row["image_count"], row["selected_count"], row["favorited_count"], row["commented_count"]) == (2, 2, 2, 2)
        for row in html
    )
    with _queries(verify_db) as api_queries:
        api = await get_dashboard(owner, verify_db)
    assert len(api_queries) == 4
    assert api.total_galleries == gallery_count
    assert all(
        (row.image_count, row.selected_count, row.favorited_count, row.commented_count) == (2, 2, 2, 2)
        for row in api.galleries
    )
    assert api.pending_signups_count is None
    # Summary queries must not hydrate child collections to aggregate in Python.
    assert all(isinstance(row, Gallery) for row in verify_db.identity_map.values())


async def test_dashboard_preserves_history_ordering_cover_policy_and_tenant_scope(
    db, verify_db, owner, other_user, monkeypatch, client, auth_headers
):
    now = datetime(2026, 11, 6, tzinfo=UTC)
    galleries = [
        Gallery(owner_id=owner.id, name="Historical", created_at=now, updated_at=now - timedelta(days=3)),
        Gallery(owner_id=owner.id, name="Small only", created_at=now - timedelta(days=1), updated_at=now),
        Gallery(
            owner_id=owner.id,
            name="No thumbnail",
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=1),
        ),
        Gallery(
            owner_id=owner.id, name="Empty", created_at=now - timedelta(days=3), updated_at=now - timedelta(days=2)
        ),
        Gallery(owner_id=other_user.id, name="Other tenant", created_at=now, updated_at=now),
    ]
    db.add_all(galleries)
    await db.flush()
    historical, small, no_thumb, empty, foreign = galleries
    images = [_image(historical, i) for i in range(4)] + [_image(small, i) for i in range(3)]
    images += [_image(no_thumb, 0), _image(foreign, 0)]
    db.add_all(images)
    visits = [
        ShareSession(gallery_id=historical.id, started_at=now - timedelta(hours=2)),
        ShareSession(gallery_id=historical.id, started_at=now - timedelta(hours=1)),
        ShareSession(gallery_id=small.id, started_at=now - timedelta(days=1)),
        ShareSession(gallery_id=foreign.id, started_at=now + timedelta(days=1)),
    ]
    db.add_all(visits)
    await db.flush()
    # Earliest image has only small, but medium on ANY later image wins;
    # smallest sort_order wins among medium previews regardless of insertion.
    db.add_all(
        [
            _preview(images[0], PreviewVariant.thumb_sm),
            _preview(images[3], PreviewVariant.thumb_md),
            _preview(images[2], PreviewVariant.thumb_md),
            _preview(images[6], PreviewVariant.thumb_sm),
            _preview(images[5], PreviewVariant.thumb_sm),
            _preview(images[7], PreviewVariant.preview),
            _preview(images[8], PreviewVariant.thumb_md),
        ]
    )
    for session in visits[:2]:
        for action in (SelectionAction.select, SelectionAction.favorite, SelectionAction.comment):
            db.add(SelectionEvent(image_id=images[0].id, share_session_id=session.id, action=action, comment="old"))
    db.add_all(
        [
            SelectionEvent(image_id=images[0].id, share_session_id=visits[1].id, action=action, created_at=now)
            for action in (SelectionAction.deselect, SelectionAction.unfavorite, SelectionAction.comment)
        ]
        + [
            SelectionEvent(
                image_id=images[1].id, share_session_id=visits[0].id, action=SelectionAction.comment, comment=""
            ),
            SelectionEvent(image_id=images[8].id, share_session_id=visits[3].id, action=SelectionAction.select),
        ]
    )
    await db.flush()
    # Deliberately contradict history to catch accidental CurrentSelection use.
    current = await db.get(CurrentSelection, images[0].id)
    current.selected = False
    current.favorited = False
    current.comment = None
    db.add(CurrentSelection(image_id=images[3].id, selected=True, favorited=True, comment="projection only"))
    await db.commit()

    signatures = []

    def signed_url(path, *, expires_in):
        signatures.append((path, expires_in))
        return f"signed:{path}"

    monkeypatch.setattr("app.frontend.dashboard.sign_url", signed_url)
    html = await _load_gallery_data(owner, verify_db)
    api = await get_dashboard(owner, verify_db)
    assert [row["gallery"].name for row in html] == ["Small only", "No thumbnail", "Empty", "Historical"]
    assert [row.name for row in api.galleries] == ["Historical", "Small only", "No thumbnail", "Empty"]
    by_id = {row["gallery"].id: row for row in html}
    expected = {
        historical.id: (4, 1, 1, 2, visits[1].started_at),
        small.id: (3, 0, 0, 0, visits[2].started_at),
        no_thumb.id: (1, 0, 0, 0, None),
        empty.id: (0, 0, 0, 0, None),
    }
    for row in api.galleries:
        values = (row.image_count, row.selected_count, row.favorited_count, row.commented_count, row.last_activity)
        assert values == expected[row.id]
        assert (
            tuple(
                by_id[row.id][key]
                for key in ("image_count", "selected_count", "favorited_count", "commented_count", "last_activity")
            )
            == values
        )
    assert by_id[historical.id]["cover_url"] == f"signed:/media/{images[2].storage_key}/thumb_md"
    assert by_id[small.id]["cover_url"] == f"signed:/media/{images[5].storage_key}/thumb_sm"
    assert by_id[no_thumb.id]["cover_url"] is None
    assert by_id[empty.id]["cover_url"] is None
    assert len(signatures) == 2 and all(ttl == 3600 for _, ttl in signatures)

    # Exercise dependency wiring as an active owner, including API route priority
    # over /{gallery_id}, and ensure another tenant receives only their gallery.
    assert (await client.get("/api/v1/galleries/dashboard")).status_code == 401
    owner_response = await client.get("/api/v1/galleries/dashboard", headers=auth_headers(owner))
    assert owner_response.status_code == 200
    assert owner_response.json() == api.model_dump(mode="json")
    other_response = await client.get("/api/v1/galleries/dashboard", headers=auth_headers(other_user))
    assert other_response.status_code == 200
    assert [row["name"] for row in other_response.json()["galleries"]] == ["Other tenant"]
    client.cookies.set("session", create_access_token(str(owner.id)))
    html_response = await client.get("/dashboard")
    assert html_response.status_code == 200
    assert "Historical" in html_response.text and "Other tenant" not in html_response.text


async def test_empty_dashboard_and_admin_pending_count(db, verify_db, owner):
    with _queries(verify_db) as html_queries:
        assert await _load_gallery_data(owner, verify_db) == []
    assert len(html_queries) == 1
    with _queries(verify_db) as api_queries:
        empty = await get_dashboard(owner, verify_db)
    assert len(api_queries) == 1
    assert empty.total_galleries == 0 and empty.pending_signups_count is None

    owner.status = UserStatus.admin
    db.add(Gallery(owner_id=owner.id, name="Admin gallery"))
    db.add_all(
        [
            PendingSignup(
                email=f"pending-{index}@test.local",
                password_hash="unused",
                verification_token_hash=b"unused",
                verification_token_salt=b"unused",
            )
            for index in range(2)
        ]
    )
    await db.commit()
    with _queries(verify_db) as admin_queries:
        admin = await get_dashboard(owner, verify_db)
    assert len(admin_queries) == 5
    assert admin.total_galleries == 1 and admin.pending_signups_count == 2
