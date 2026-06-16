"""Tests for the notification system.

Verifies that:
- Notification schemas validate correctly
- SSRF protection blocks internal webhook URLs
- Webhook requires HTTPS
- Service module has correct dispatch structure
- Router endpoints are registered with correct auth
- Email templates exist for all event types
- Complete-review triggers notification
- Delivery log is tenant-isolated
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.notifications.schemas import (
    BLOCKED_HOSTS,
    VALID_EVENT_TYPES,
    NotificationConfigUpdate,
    NotificationDeliveryResponse,
)


class TestNotificationSchemas:
    """Verify schema validation rules."""

    def test_valid_config(self) -> None:
        config = NotificationConfigUpdate(
            email_enabled=True,
            webhook_url="https://hooks.example.com/notify",
            events=["gallery_completed"],
        )
        assert config.email_enabled is True
        assert config.webhook_url == "https://hooks.example.com/notify"

    def test_invalid_event_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown event types"):
            NotificationConfigUpdate(events=["nonexistent_event"])

    def test_all_valid_events_accepted(self) -> None:
        config = NotificationConfigUpdate(events=list(VALID_EVENT_TYPES))
        assert set(config.events) == VALID_EVENT_TYPES

    def test_empty_events_accepted(self) -> None:
        config = NotificationConfigUpdate(events=[])
        assert config.events == []

    def test_webhook_url_optional(self) -> None:
        config = NotificationConfigUpdate(events=["gallery_completed"])
        assert config.webhook_url is None

    def test_delivery_response_has_all_fields(self) -> None:
        fields = NotificationDeliveryResponse.model_fields
        assert "event" in fields
        assert "status" in fields
        assert "error_message" in fields
        assert "attempted_at" in fields


class TestSSRFProtection:
    """Verify webhook URL validation blocks internal addresses."""

    @pytest.mark.parametrize(
        "host",
        [
            "https://localhost/hook",
            "https://127.0.0.1/hook",
            "https://0.0.0.0/hook",
            "https://metadata.google.internal/hook",
        ],
    )
    def test_blocked_hosts_rejected(self, host: str) -> None:
        with pytest.raises(ValidationError, match="internal addresses"):
            NotificationConfigUpdate(
                webhook_url=host,
                events=["gallery_completed"],
            )

    def test_internal_domain_rejected(self) -> None:
        with pytest.raises(ValidationError, match="internal addresses"):
            NotificationConfigUpdate(
                webhook_url="https://service.internal/hook",
                events=["gallery_completed"],
            )

    def test_local_domain_rejected(self) -> None:
        with pytest.raises(ValidationError, match="internal addresses"):
            NotificationConfigUpdate(
                webhook_url="https://myhost.local/hook",
                events=["gallery_completed"],
            )

    def test_http_rejected(self) -> None:
        with pytest.raises(ValidationError, match="HTTPS"):
            NotificationConfigUpdate(
                webhook_url="http://hooks.example.com/notify",
                events=["gallery_completed"],
            )

    def test_valid_https_url_accepted(self) -> None:
        config = NotificationConfigUpdate(
            webhook_url="https://hooks.example.com/notify",
            events=["gallery_completed"],
        )
        assert config.webhook_url == "https://hooks.example.com/notify"

    def test_blocked_hosts_list_covers_essentials(self) -> None:
        assert "localhost" in BLOCKED_HOSTS
        assert "127.0.0.1" in BLOCKED_HOSTS
        assert "169.254.169.254" in BLOCKED_HOSTS
        assert "metadata.google.internal" in BLOCKED_HOSTS


class TestNotificationService:
    """Verify service module structure."""

    def test_send_notification_exists(self) -> None:
        from app.notifications.service import send_notification

        assert callable(send_notification)

    def test_notify_all_admins_exists(self) -> None:
        from app.notifications.service import notify_all_admins

        assert callable(notify_all_admins)

    def test_subject_map_covers_all_events(self) -> None:
        from app.notifications.service import SUBJECT_MAP

        for event in VALID_EVENT_TYPES:
            assert event in SUBJECT_MAP, f"Missing subject template for {event}"

    def test_service_logs_delivery(self) -> None:
        with open("app/notifications/service.py") as f:
            source = f.read()
        assert "NotificationDelivery" in source
        assert "delivery.status" in source


class TestNotificationRouter:
    """Verify router endpoints and auth."""

    def test_router_prefix(self) -> None:
        from app.notifications.router import router

        assert router.prefix == "/api/v1/notifications"

    def test_config_endpoint_uses_active_user(self) -> None:
        with open("app/notifications/router.py") as f:
            source = f.read()
        assert "require_active_user" in source

    def test_deliveries_filtered_by_user(self) -> None:
        with open("app/notifications/router.py") as f:
            source = f.read()
        assert "NotificationConfig.user_id == user.id" in source

    def test_deliveries_limit_clamped(self) -> None:
        with open("app/notifications/router.py") as f:
            source = f.read()
        assert "clamped_limit" in source

    def test_router_registered_in_main(self) -> None:
        with open("app/main.py") as f:
            source = f.read()
        assert "notifications_router" in source


class TestEmailTemplates:
    """Verify email templates exist for all event types."""

    @pytest.mark.parametrize("event", list(VALID_EVENT_TYPES))
    def test_html_template_exists(self, event: str) -> None:
        path = Path("app/templates/email") / f"{event}.html"
        assert path.exists(), f"Missing HTML template: {path}"

    @pytest.mark.parametrize("event", list(VALID_EVENT_TYPES))
    def test_text_template_exists(self, event: str) -> None:
        path = Path("app/templates/email") / f"{event}.txt"
        assert path.exists(), f"Missing text template: {path}"


class TestCompleteReviewNotification:
    """Verify complete-review triggers notification."""

    def test_complete_review_calls_send_notification(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "send_notification" in source
        assert '"gallery_completed"' in source

    def test_notification_failure_does_not_break_response(self) -> None:
        with open("app/guest/router.py") as f:
            source = f.read()
        assert "except Exception" in source
        assert "Failed to send gallery_completed notification" in source


class TestNotifyAdminsSignup:
    """Verify the guaranteed signup-alert path (system override, config-free)."""

    @staticmethod
    def _db_returning_admin_emails(emails: list[str]) -> MagicMock:
        """Build a mock AsyncSession whose execute() yields the given admin emails."""
        db = MagicMock()
        result = MagicMock()
        result.all.return_value = [(e,) for e in emails]
        db.execute = AsyncMock(return_value=result)
        return db

    @pytest.mark.asyncio
    async def test_emails_every_admin(self) -> None:
        from app.notifications import service

        db = self._db_returning_admin_emails(["a@example.com", "b@example.com"])
        with (
            patch.object(service.settings, "notify_admins_on_signup", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_admins_signup("newuser@example.com", db)

        assert send_mock.await_count == 2
        recipients = {call.args[0]["To"] for call in send_mock.await_args_list}
        assert recipients == {"a@example.com", "b@example.com"}

    @pytest.mark.asyncio
    async def test_disabled_by_setting(self) -> None:
        from app.notifications import service

        db = self._db_returning_admin_emails(["a@example.com"])
        with (
            patch.object(service.settings, "notify_admins_on_signup", False),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_admins_signup("newuser@example.com", db)

        send_mock.assert_not_awaited()
        db.execute.assert_not_awaited()  # short-circuits before any DB hit

    @pytest.mark.asyncio
    async def test_no_smtp_host_skips(self) -> None:
        from app.notifications import service

        db = self._db_returning_admin_emails(["a@example.com"])
        with (
            patch.object(service.settings, "notify_admins_on_signup", True),
            patch.object(service.settings, "smtp_host", ""),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_admins_signup("newuser@example.com", db)

        send_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_admins_no_send(self) -> None:
        from app.notifications import service

        db = self._db_returning_admin_emails([])
        with (
            patch.object(service.settings, "notify_admins_on_signup", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_admins_signup("newuser@example.com", db)

        send_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_per_recipient_failure_isolated(self) -> None:
        from app.notifications import service

        db = self._db_returning_admin_emails(["a@example.com", "b@example.com"])
        # First send raises, second must still be attempted; call must not propagate.
        send_mock = AsyncMock(side_effect=[RuntimeError("smtp down"), None])
        with (
            patch.object(service.settings, "notify_admins_on_signup", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=send_mock),
        ):
            await service.notify_admins_signup("newuser@example.com", db)

        assert send_mock.await_count == 2  # second recipient still attempted

    def test_function_exists(self) -> None:
        from app.notifications.service import notify_admins_signup

        assert callable(notify_admins_signup)


class TestSendVerificationEmail:
    """Verify the config-free verification-email path (picture-stage-x8t)."""

    def test_function_exists(self) -> None:
        from app.notifications.service import send_verification_email

        assert callable(send_verification_email)

    def test_subject_and_templates_present(self) -> None:
        from app.notifications.service import SUBJECT_MAP

        assert "verify_email" in SUBJECT_MAP
        assert Path("app/templates/email/verify_email.html").exists()
        assert Path("app/templates/email/verify_email.txt").exists()

    @pytest.mark.asyncio
    async def test_sends_to_user_with_token_link(self) -> None:
        from app.notifications import service

        with (
            patch.object(service.settings, "send_verification_email_enabled", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.settings, "app_url", "https://picture.example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.send_verification_email("user@example.com", "tok-abc123", MagicMock())

        assert send_mock.await_count == 1
        msg = send_mock.await_args.args[0]
        assert msg["To"] == "user@example.com"
        # Plaintext token must reach the user via an HTTPS link built from app_url.
        body = msg.get_payload()[0].get_payload(decode=True).decode()
        assert "https://picture.example.com/verify-email/tok-abc123" in body

    @pytest.mark.asyncio
    async def test_disabled_by_setting(self) -> None:
        from app.notifications import service

        with (
            patch.object(service.settings, "send_verification_email_enabled", False),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.send_verification_email("user@example.com", "tok", MagicMock())

        send_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_smtp_host_skips(self) -> None:
        from app.notifications import service

        with (
            patch.object(service.settings, "send_verification_email_enabled", True),
            patch.object(service.settings, "smtp_host", ""),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.send_verification_email("user@example.com", "tok", MagicMock())

        send_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_failure_does_not_propagate(self) -> None:
        from app.notifications import service

        send_mock = AsyncMock(side_effect=RuntimeError("smtp down"))
        with (
            patch.object(service.settings, "send_verification_email_enabled", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=send_mock),
        ):
            # Must swallow the error — a signup must never fail on mail delivery.
            await service.send_verification_email("user@example.com", "tok", MagicMock())

        assert send_mock.await_count == 1

    @pytest.mark.parametrize("path", ["app/auth/router.py", "app/frontend/auth.py"])
    def test_both_signup_paths_wire_verification_email(self, path: str) -> None:
        with open(path) as f:
            source = f.read()
        # Wired with the freshly generated plaintext token, failure-isolated.
        assert "send_verification_email(" in source
        assert "verification_token" in source
        assert "Failed to send verification email" in source
        # The dead ebm.7 TODO marker must be gone.
        assert "TODO: send verification email" not in source


class TestNotifyOwnerGalleryCompleted:
    """Verify the guaranteed completion-alert to the gallery owner (config-free)."""

    @staticmethod
    def _payload() -> dict:
        return {
            "gallery_name": "Sunset Shoot",
            "total_images": 12,
            "selected_count": 5,
            "favorited_count": 2,
            "gallery_url": "https://example.com/galleries/00000000-0000-0000-0000-000000000000",
        }

    @pytest.mark.asyncio
    async def test_emails_owner(self) -> None:
        from app.notifications import service

        with (
            patch.object(service.settings, "notify_owner_on_completion", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.settings, "smtp_from", "noreply@example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_owner_gallery_completed("owner@example.com", self._payload(), MagicMock())

        assert send_mock.await_count == 1
        msg = send_mock.await_args.args[0]
        assert msg["To"] == "owner@example.com"
        assert "Sunset Shoot" in msg["Subject"]

    @pytest.mark.asyncio
    async def test_disabled_by_setting(self) -> None:
        from app.notifications import service

        with (
            patch.object(service.settings, "notify_owner_on_completion", False),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_owner_gallery_completed("owner@example.com", self._payload(), MagicMock())

        send_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_smtp_host_skips(self) -> None:
        from app.notifications import service

        with (
            patch.object(service.settings, "notify_owner_on_completion", True),
            patch.object(service.settings, "smtp_host", ""),
            patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send_mock,
        ):
            await service.notify_owner_gallery_completed("owner@example.com", self._payload(), MagicMock())

        send_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_failure_is_swallowed(self) -> None:
        """A broken SMTP server must never propagate — completing a review must succeed."""
        from app.notifications import service

        with (
            patch.object(service.settings, "notify_owner_on_completion", True),
            patch.object(service.settings, "smtp_host", "smtp.example.com"),
            patch.object(service.settings, "smtp_from", "noreply@example.com"),
            patch.object(service.aiosmtplib, "send", new=AsyncMock(side_effect=OSError("smtp down"))),
        ):
            # Must not raise.
            await service.notify_owner_gallery_completed("owner@example.com", self._payload(), MagicMock())

    def test_complete_review_calls_notify_owner(self) -> None:
        """The completion endpoint wires the config-free owner alert."""
        source = Path("app/guest/router.py").read_text()
        assert "notify_owner_gallery_completed" in source
