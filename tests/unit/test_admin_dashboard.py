"""Tests for the dashboard endpoint.

Verifies that:
- Dashboard schema has all required fields
- Endpoint is registered at /dashboard (not /{gallery_id})
- Pending signups counter is only visible for admins
- Selection counts are present in gallery response
- Endpoint uses require_active_user (not require_admin)
"""



class TestDashboardSchemas:
    """Verify dashboard response schemas."""

    def test_dashboard_gallery_has_progress_fields(self) -> None:
        from app.galleries.schemas import DashboardGalleryResponse
        fields = DashboardGalleryResponse.model_fields
        assert "selected_count" in fields
        assert "favorited_count" in fields
        assert "commented_count" in fields

    def test_dashboard_gallery_has_status_and_meta(self) -> None:
        from app.galleries.schemas import DashboardGalleryResponse
        fields = DashboardGalleryResponse.model_fields
        assert "status" in fields
        assert "image_count" in fields
        assert "has_share_token" in fields
        assert "last_activity" in fields

    def test_dashboard_response_has_galleries_and_count(self) -> None:
        from app.galleries.schemas import DashboardResponse
        fields = DashboardResponse.model_fields
        assert "galleries" in fields
        assert "total_galleries" in fields

    def test_dashboard_response_has_optional_pending_signups(self) -> None:
        from app.galleries.schemas import DashboardResponse
        field = DashboardResponse.model_fields["pending_signups_count"]
        assert field.default is None

    def test_dashboard_response_serializes_with_none_pending(self) -> None:
        from app.galleries.schemas import DashboardResponse
        resp = DashboardResponse(galleries=[], total_galleries=0)
        assert resp.pending_signups_count is None

    def test_dashboard_response_serializes_with_pending_count(self) -> None:
        from app.galleries.schemas import DashboardResponse
        resp = DashboardResponse(
            galleries=[], total_galleries=0, pending_signups_count=5
        )
        assert resp.pending_signups_count == 5


class TestDashboardEndpointStructure:
    """Verify endpoint registration and auth."""

    def test_dashboard_endpoint_exists(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert '"/dashboard"' in source or "'/dashboard'" in source

    def test_dashboard_uses_active_user_not_admin(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        idx = source.index("dashboard")
        endpoint_block = source[idx:idx + 800]
        assert "require_active_user" in endpoint_block
        assert "require_admin" not in endpoint_block

    def test_dashboard_checks_admin_for_pending_signups(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "UserStatus.admin" in source
        assert "PendingSignup" in source

    def test_dashboard_queries_selection_events(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "SelectionEvent" in source
        assert "SelectionAction.select" in source
        assert "SelectionAction.favorite" in source

    def test_dashboard_queries_last_activity(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "ShareSession" in source
        assert "last_activity" in source

    def test_dashboard_filters_by_owner(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "Gallery.owner_id == user.id" in source
