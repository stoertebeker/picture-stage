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
