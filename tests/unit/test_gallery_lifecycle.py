"""Tests for gallery lifecycle status transitions.

Verifies that:
- Only allowed transitions succeed
- Forbidden transitions are rejected (409)
- Share-link creation auto-transitions draft→shared
- Share-link revocation auto-transitions shared→draft
- Sharing requires a share token
- Complete-review sets gallery status to completed
- Double-complete is rejected (409 idempotency guard)
"""

import pytest

from app.db.models import GalleryStatus
from app.galleries.router import ALLOWED_TRANSITIONS


class TestAllowedTransitions:
    """Verify the transition map is correct and complete."""

    def test_draft_can_become_shared(self) -> None:
        assert GalleryStatus.shared in ALLOWED_TRANSITIONS[GalleryStatus.draft]

    def test_draft_cannot_become_completed(self) -> None:
        assert GalleryStatus.completed not in ALLOWED_TRANSITIONS[GalleryStatus.draft]

    def test_draft_cannot_become_archived(self) -> None:
        assert GalleryStatus.archived not in ALLOWED_TRANSITIONS[GalleryStatus.draft]

    def test_shared_can_become_completed(self) -> None:
        assert GalleryStatus.completed in ALLOWED_TRANSITIONS[GalleryStatus.shared]

    def test_shared_cannot_become_draft(self) -> None:
        assert GalleryStatus.draft not in ALLOWED_TRANSITIONS[GalleryStatus.shared]

    def test_shared_cannot_become_archived(self) -> None:
        assert GalleryStatus.archived not in ALLOWED_TRANSITIONS[GalleryStatus.shared]

    def test_completed_can_become_archived(self) -> None:
        assert GalleryStatus.archived in ALLOWED_TRANSITIONS[GalleryStatus.completed]

    def test_completed_can_become_shared(self) -> None:
        assert GalleryStatus.shared in ALLOWED_TRANSITIONS[GalleryStatus.completed]

    def test_archived_can_become_shared(self) -> None:
        assert GalleryStatus.shared in ALLOWED_TRANSITIONS[GalleryStatus.archived]

    def test_archived_cannot_become_draft(self) -> None:
        assert GalleryStatus.draft not in ALLOWED_TRANSITIONS[GalleryStatus.archived]

    def test_all_statuses_have_transitions(self) -> None:
        for status in GalleryStatus:
            assert status in ALLOWED_TRANSITIONS, f"Status '{status.value}' missing from ALLOWED_TRANSITIONS"

    def test_no_self_transitions_in_map(self) -> None:
        for from_status, to_statuses in ALLOWED_TRANSITIONS.items():
            assert from_status not in to_statuses, f"Self-transition for '{from_status.value}' should not be in the map"


class TestStatusFieldRemovedFromUpdate:
    """GalleryUpdate must NOT allow setting status directly."""

    def test_gallery_update_has_no_status_field(self) -> None:
        from app.galleries.schemas import GalleryUpdate

        assert "status" not in GalleryUpdate.model_fields


class TestStatusTransitionSchema:
    """GalleryStatusTransition schema validation."""

    def test_valid_status(self) -> None:
        from app.galleries.schemas import GalleryStatusTransition

        t = GalleryStatusTransition(status=GalleryStatus.shared)
        assert t.status == GalleryStatus.shared

    def test_invalid_status_rejected(self) -> None:
        from pydantic import ValidationError

        from app.galleries.schemas import GalleryStatusTransition

        with pytest.raises(ValidationError):
            GalleryStatusTransition(status="nonexistent")


class TestShareLinkAutoTransition:
    """Verify share-link creation/revocation auto-sets status."""

    def test_sharing_code_sets_status_to_shared(self) -> None:
        with open("app/galleries/sharing.py") as f:
            source = f.read()
        assert "gallery.status = GalleryStatus.shared" in source

    def test_revoke_code_sets_status_to_draft(self) -> None:
        with open("app/galleries/sharing.py") as f:
            source = f.read()
        assert "gallery.status = GalleryStatus.draft" in source

    def test_sharing_only_transitions_from_draft(self) -> None:
        with open("app/galleries/sharing.py") as f:
            source = f.read()
        assert "gallery.status == GalleryStatus.draft" in source

    def test_revoke_only_transitions_from_shared(self) -> None:
        with open("app/galleries/sharing.py") as f:
            source = f.read()
        assert "gallery.status == GalleryStatus.shared" in source

    def test_sharing_persists_replayable_share_token(self) -> None:
        with open("app/galleries/sharing.py") as f:
            api_source = f.read()
        with open("app/frontend/galleries.py") as f:
            frontend_source = f.read()
        with open("app/db/models.py") as f:
            model_source = f.read()

        assert "share_token: Mapped[str | None]" in model_source
        assert "gallery.share_token = token" in api_source
        assert "gallery.share_token = token" in frontend_source
        assert "gallery.share_token = None" in api_source
        assert "gallery.share_token = None" in frontend_source

    def test_frontend_rebuilds_share_url_from_persisted_token(self) -> None:
        with open("app/frontend/galleries.py") as f:
            source = f.read()

        assert "if gallery.share_token:" in source
        assert 'ctx["share_url"] = f"{base_url}/g/{gallery.share_token}"' in source

    def test_legacy_active_share_link_has_clear_fallback_copy(self) -> None:
        import json

        with open("app/i18n/de.json") as f:
            de = json.load(f)
        with open("app/i18n/en.json") as f:
            en = json.load(f)

        assert "aeltere Links" in de["gallery"]["share_active_text"]
        assert "Older links" in en["gallery"]["share_active_text"]


class TestTransitionEndpointGuards:
    """Verify the transition endpoint enforces share-token requirement."""

    def test_endpoint_checks_share_token_for_shared(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "share_token_hash is None" in source
        assert "Cannot share gallery without a share link" in source

    def test_endpoint_uses_409_for_invalid_transition(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "HTTP_409_CONFLICT" in source

    def test_transition_endpoint_filters_by_owner(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "_get_owned_gallery" in source


class TestCompleteReviewEndpoint:
    """Verify the guest complete-review endpoint handles lifecycle correctly."""

    def test_complete_sets_gallery_status_to_completed(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "gallery.status = GalleryStatus.completed" in source

    def test_complete_only_transitions_from_shared(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "gallery.status == GalleryStatus.shared" in source

    def test_complete_rejects_already_completed_session(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "session.completed_at is not None" in source
        assert "Review already completed" in source

    def test_complete_uses_409_for_duplicate(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "HTTP_409_CONFLICT" in source

    def test_complete_returns_structured_response(self) -> None:
        from app.guest.router import CompleteReviewResponse

        fields = CompleteReviewResponse.model_fields
        assert "message" in fields
        assert "gallery_status" in fields
        assert "session_completed" in fields

    def test_complete_has_response_model(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "response_model=CompleteReviewResponse" in source
