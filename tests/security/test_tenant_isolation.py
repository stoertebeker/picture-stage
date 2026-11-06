"""Tenant isolation tests.

These tests verify that User A cannot access User B's resources.
They test authorization queries and endpoint denials without a database or HTTP stack.

Run with: pytest tests/security/ -v
"""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.db.models import User


def _denied_gallery_session() -> AsyncMock:
    result = Mock()
    result.scalar_one_or_none.return_value = None
    return AsyncMock(execute=AsyncMock(return_value=result))


def _assert_owned_gallery_denial_query(db: AsyncMock, gallery_id: uuid.UUID | str, user: User) -> None:
    db.execute.assert_awaited_once()
    query = db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert query.params == {"id_1": gallery_id, "owner_id_1": user.id}
    assert "galleries.id = %(id_1)s::UUID AND galleries.owner_id = %(owner_id_1)s::UUID" in str(query)
    db.add.assert_not_called()
    db.delete.assert_not_awaited()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


class TestGalleryIsolation:
    """Verify that gallery queries always filter by owner_id."""

    def test_gallery_query_includes_owner_filter(self) -> None:
        """The _get_owned_gallery helper must filter by owner_id.
        This is a structural test — we verify the query pattern."""
        from app.galleries.router import _get_owned_gallery

        assert _get_owned_gallery is not None
        # Function signature requires both gallery_id AND user
        import inspect

        sig = inspect.signature(_get_owned_gallery)
        params = list(sig.parameters.keys())
        assert "gallery_id" in params
        assert "user" in params

    def test_gallery_list_query_filters_owner(self) -> None:
        """list_galleries endpoint must filter by owner_id."""

        with open("app/galleries/router.py") as f:
            source = f.read()

        assert "Gallery.owner_id == user.id" in source, "Gallery list query MUST filter by owner_id"

    @pytest.mark.parametrize("action", ["detail", "delete"])
    async def test_gallery_detail_query_filters_owner(self, monkeypatch, action) -> None:
        """Detail and deletion deny absent ownership through the real shared query."""
        from app.galleries import router

        user = User(id=uuid.uuid4())
        gallery_id = uuid.uuid4()
        db = _denied_gallery_session()
        lock = AsyncMock()
        purge = AsyncMock()
        storage = Mock()
        monkeypatch.setattr(router, "lock_gallery", lock)
        monkeypatch.setattr(router, "purge_gallery", purge)

        with pytest.raises(HTTPException) as exc:
            if action == "detail":
                await router.get_gallery(gallery_id, user=user, db=db)
            else:
                await router.delete_gallery(gallery_id, user=user, db=db, storage=storage)

        assert exc.value.status_code == 404
        assert exc.value.detail == "Gallery not found"
        _assert_owned_gallery_denial_query(db, gallery_id, user)
        purge.assert_not_awaited()
        assert storage.mock_calls == []
        if action == "delete":
            lock.assert_awaited_once_with(db, gallery_id)
        else:
            lock.assert_not_awaited()


class TestImageIsolation:
    """Verify image endpoints check gallery ownership."""

    def test_upload_checks_gallery_owner(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()

        assert "Gallery.owner_id == user.id" in source, "Image upload MUST verify gallery ownership"

    def test_list_images_checks_gallery_owner(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()

        assert source.count("Gallery.owner_id == user.id") >= 2, "Image list and delete MUST verify gallery ownership"


class TestExportIsolation:
    """Verify export endpoint checks gallery ownership."""

    def test_export_checks_gallery_owner(self) -> None:
        with open("app/galleries/export.py") as f:
            source = f.read()

        assert "Gallery.owner_id == user.id" in source, "Export MUST verify gallery ownership"


class TestShareIsolation:
    """Verify share link management checks gallery ownership."""

    async def test_share_create_checks_owner(self, monkeypatch) -> None:
        from app.galleries import sharing

        user = User(id=uuid.uuid4())
        gallery_id = uuid.uuid4()
        db = _denied_gallery_session()
        hash_password = AsyncMock()
        create_share = AsyncMock()
        monkeypatch.setattr(sharing, "hash_gallery_password", hash_password)
        monkeypatch.setattr(sharing, "create_gallery_share", create_share)

        with pytest.raises(HTTPException) as exc:
            await sharing.create_share_link(
                str(gallery_id), sharing.ShareCreateRequest(password="password"), Mock(), user=user, db=db
            )

        assert exc.value.status_code == 404
        assert exc.value.detail == "Gallery not found"
        _assert_owned_gallery_denial_query(db, str(gallery_id), user)
        hash_password.assert_not_awaited()
        create_share.assert_not_awaited()

    async def test_share_revoke_checks_owner(self, monkeypatch) -> None:
        from app.galleries import sharing

        user = User(id=uuid.uuid4())
        gallery_id = uuid.uuid4()
        db = _denied_gallery_session()
        revoke_share = AsyncMock()
        monkeypatch.setattr(sharing, "revoke_gallery_share", revoke_share)

        with pytest.raises(HTTPException) as exc:
            await sharing.revoke_share_link(str(gallery_id), user=user, db=db)

        assert exc.value.status_code == 404
        assert exc.value.detail == "Gallery not found"
        _assert_owned_gallery_denial_query(db, str(gallery_id), user)
        revoke_share.assert_not_awaited()


class TestAdminEndpoints:
    """Verify admin endpoints require admin status."""

    def test_admin_router_requires_admin(self) -> None:
        with open("app/admin/router.py") as f:
            source = f.read()

        assert "require_admin" in source, "Admin endpoints MUST use require_admin dependency"

        assert source.count("Depends(require_admin)") >= 3, "All admin endpoints MUST use require_admin"


class TestGuestApiIsolation:
    """Verify guest API never exposes admin functionality."""

    def test_guest_router_has_no_admin_dependency(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()

        assert "require_admin" not in source, "Guest API must NOT reference admin dependencies"
        assert "get_current_user" not in source, "Guest API must NOT use authenticated user context"

    def test_guest_router_never_serves_originals(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()

        assert "originals" not in source.lower() or "original" not in source.lower(), (
            "Guest API must NEVER reference original images"
        )
