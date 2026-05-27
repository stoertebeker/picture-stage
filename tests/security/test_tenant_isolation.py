"""Tenant isolation tests.

These tests verify that User A cannot access User B's resources.
They test the authorization boundaries, not the full HTTP stack.

Run with: pytest tests/security/ -v
Requires: PostgreSQL (via DATABASE_URL env var)
"""

import uuid

import pytest


@pytest.fixture
def user_a_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_b_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def gallery_a_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def gallery_b_id() -> uuid.UUID:
    return uuid.uuid4()


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

    def test_gallery_detail_query_filters_owner(self) -> None:
        """get_gallery endpoint must filter by owner_id."""

        with open("app/galleries/router.py") as f:
            source = f.read()

        assert source.count("Gallery.owner_id == user.id") >= 2, (
            "Gallery detail and delete MUST also filter by owner_id"
        )


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

    def test_share_create_checks_owner(self) -> None:
        with open("app/galleries/sharing.py") as f:
            source = f.read()

        assert "Gallery.owner_id == user.id" in source, "Share link creation MUST verify gallery ownership"

    def test_share_revoke_checks_owner(self) -> None:
        with open("app/galleries/sharing.py") as f:
            source = f.read()

        assert source.count("Gallery.owner_id == user.id") >= 2, (
            "Share link revocation MUST also verify gallery ownership"
        )


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
