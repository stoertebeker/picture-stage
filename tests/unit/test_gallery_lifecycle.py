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

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.auth.passwords import verify_token
from app.auth.token_lookup import token_locator
from app.db.models import ALLOWED_TRANSITIONS, Gallery, GalleryStatus
from app.galleries import management


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

    @pytest.mark.parametrize("initial_status", list(GalleryStatus))
    async def test_sharing_rotates_credentials_after_reload(self, monkeypatch, initial_status) -> None:
        gallery = Gallery(id=uuid.uuid4(), owner_id=uuid.uuid4(), status=initial_status, authorization_epoch=7)
        db = AsyncMock()
        lock = AsyncMock(return_value=gallery)
        monkeypatch.setattr(management, "lock_owned_gallery_authorization", lock)

        saved, token = await management.create_gallery_share(gallery.id, gallery.owner_id, db, password_hash="hash")

        lock.assert_awaited_once_with(gallery.id, gallery.owner_id, db)
        assert saved is gallery
        assert gallery.authorization_epoch == 8
        assert gallery.password_hash == "hash"
        assert gallery.share_token == token
        assert verify_token(token, gallery.share_token_hash, gallery.share_token_salt)
        assert gallery.share_token_lookup == token_locator(token)
        assert gallery.status == (GalleryStatus.shared if initial_status == GalleryStatus.draft else initial_status)
        db.commit.assert_not_awaited()

    @pytest.mark.parametrize("initial_status", list(GalleryStatus))
    async def test_revoking_clears_credentials_after_reload(self, monkeypatch, initial_status) -> None:
        gallery = Gallery(
            id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            status=initial_status,
            authorization_epoch=7,
            share_token="old",
            share_token_hash=b"old-hash",
            share_token_salt=b"old-salt",
            share_token_lookup="old-lookup",
            password_hash="old-password",
        )
        db = AsyncMock()
        lock = AsyncMock(return_value=gallery)
        monkeypatch.setattr(management, "lock_owned_gallery_authorization", lock)

        saved = await management.revoke_gallery_share(gallery.id, gallery.owner_id, db)

        lock.assert_awaited_once_with(gallery.id, gallery.owner_id, db)
        assert saved is gallery
        assert gallery.authorization_epoch == 8
        assert gallery.password_hash is None
        assert gallery.share_token is gallery.share_token_hash is gallery.share_token_salt is None
        assert gallery.share_token_lookup is None
        assert gallery.status == (GalleryStatus.draft if initial_status == GalleryStatus.shared else initial_status)
        db.commit.assert_not_awaited()

    def test_frontend_rebuilds_share_url_from_persisted_token(self) -> None:
        with open("app/frontend/galleries.py") as f:
            source = f.read()

        assert "if gallery.share_token:" in source
        assert 'ctx["share_url"] = build_share_url(request, gallery.share_token)' in source

    def test_legacy_active_share_link_has_clear_fallback_copy(self) -> None:
        import json

        with open("app/i18n/de.json") as f:
            de = json.load(f)
        with open("app/i18n/en.json") as f:
            en = json.load(f)

        assert "ältere Links" in de["gallery"]["share_active_text"]
        assert "Older links" in en["gallery"]["share_active_text"]


class TestTransitionEndpointGuards:
    """Verify the transition endpoint enforces share-token requirement."""

    @pytest.mark.parametrize("initial_status", [GalleryStatus.draft, GalleryStatus.completed, GalleryStatus.archived])
    def test_sharing_requires_token(self, initial_status) -> None:
        gallery = Gallery(status=initial_status)
        with pytest.raises(HTTPException) as exc:
            management.change_gallery_status(gallery, GalleryStatus.shared)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Cannot share gallery without a share link. Create one first."
        assert gallery.status == initial_status

    @pytest.mark.parametrize("initial_status", list(GalleryStatus))
    @pytest.mark.parametrize("target", list(GalleryStatus))
    def test_status_transition_rules_and_noop(self, initial_status, target) -> None:
        gallery = Gallery(status=initial_status, share_token_hash=b"token-hash")
        if target == initial_status:
            # A no-op remains valid even if no share credentials exist.
            gallery.share_token_hash = None
            assert management.change_gallery_status(gallery, target) is False
        elif target in ALLOWED_TRANSITIONS[initial_status]:
            assert management.change_gallery_status(gallery, target) is True
        else:
            with pytest.raises(HTTPException) as exc:
                management.change_gallery_status(gallery, target)
            assert exc.value.status_code == 409
            assert exc.value.detail == f"Transition from '{initial_status.value}' to '{target.value}' is not allowed"
            assert gallery.status == initial_status
            return
        assert gallery.status == target

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
