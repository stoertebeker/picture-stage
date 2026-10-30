"""Single-recipient signup and SMTP delivery regressions (picture-stage-e1nt)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.auth.schemas import SignupRequest
from app.frontend import auth
from app.notifications import service
from app.security.rate_limit import limiter


@pytest.mark.parametrize(
    "email",
    ["first@example.com, second@example.com", "first@example.com\nBcc: second@example.com"],
)
async def test_browser_and_api_signup_reject_recipient_lists_before_db(email: str) -> None:
    request = MagicMock()
    request.form = AsyncMock(
        return_value={"email": email, "password": "long-enough", "password_confirm": "long-enough"}
    )
    request.cookies = {}
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    previous = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(auth.templates, "TemplateResponse", return_value=MagicMock()) as render:
            await auth.signup_submit(request, db)
        assert render.call_args.kwargs["status_code"] == 422
        db.execute.assert_not_awaited()
        db.add.assert_not_called()
        db.commit.assert_not_awaited()
        with pytest.raises(ValidationError):
            SignupRequest(email=email, password="long-enough")
    finally:
        limiter.enabled = previous


async def test_browser_signup_preserves_single_mailbox() -> None:
    request = MagicMock()
    request.form = AsyncMock(
        return_value={
            "email": "Person <person@EXAMPLE.COM>",
            "password": "long-enough",
            "password_confirm": "long-enough",
        }
    )
    request.cookies = {}
    request.headers = {}
    request.client.host = "127.0.0.1"
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    previous = limiter.enabled
    limiter.enabled = False
    try:
        with (
            patch.object(auth.templates, "TemplateResponse", return_value=MagicMock()),
            patch.object(auth, "notify_admins_signup", new=AsyncMock()) as admin,
            patch.object(auth, "send_verification_email", new=AsyncMock()) as verification,
        ):
            await auth.signup_submit(request, db)
        assert db.add.call_args.args[0].email == "Person <person@EXAMPLE.COM>"
        admin.assert_awaited_once_with("Person <person@EXAMPLE.COM>", db)
        assert verification.await_args.args[0] == "Person <person@EXAMPLE.COM>"
        db.commit.assert_awaited_once()
    finally:
        limiter.enabled = previous


@pytest.mark.parametrize(
    "email",
    [
        "a@example.com,b@example.com",
        "a@example.com\r\nBcc: b@example.com",
        "Admin <a@local.test>, b@local.test",
        "Admin <a@local.test>\r\nBcc: b@local.test",
    ],
)
async def test_verification_sink_rejects_legacy_recipient_lists(email: str) -> None:
    with (
        patch.object(service.settings, "send_verification_email_enabled", True),
        patch.object(service.settings, "smtp_host", "smtp.example.com"),
        patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send,
    ):
        await service.send_verification_email(email, "token", MagicMock())
        send.assert_not_awaited()


async def test_verification_envelope_has_one_normalized_recipient() -> None:
    with (
        patch.object(service.settings, "send_verification_email_enabled", True),
        patch.object(service.settings, "smtp_host", "smtp.example.com"),
        patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send,
    ):
        await service.send_verification_email("Person <person@EXAMPLE.COM>", "token", MagicMock())
    assert send.await_args.kwargs["recipients"] == ["person@example.com"]
    assert send.await_args.args[0]["To"] == "person@example.com"


@pytest.mark.parametrize("mailbox", ["admin@local.test", "Administrator <admin@local.test>"])
async def test_existing_local_domain_can_receive_mail(mailbox: str) -> None:
    with (
        patch.object(service.settings, "send_verification_email_enabled", True),
        patch.object(service.settings, "smtp_host", "smtp.example.com"),
        patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send,
    ):
        await service.send_verification_email(mailbox, "token", MagicMock())
    assert send.await_args.kwargs["recipients"] == ["admin@local.test"]


async def test_existing_invalid_admin_is_skipped_and_valid_admin_gets_one_envelope_recipient() -> None:
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = [("a@example.com,b@example.com",), ("good@example.com",)]
    db.execute = AsyncMock(return_value=result)
    with (
        patch.object(service.settings, "notify_admins_on_signup", True),
        patch.object(service.settings, "smtp_host", "smtp.example.com"),
        patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send,
    ):
        await service.notify_admins_signup("new@example.com", db)
    send.assert_awaited_once()
    assert send.await_args.kwargs["recipients"] == ["good@example.com"]


@pytest.mark.parametrize("email,should_send", [("one@example.com,two@example.com", False), ("one@example.com", True)])
async def test_owner_notification_requires_one_mailbox(email: str, should_send: bool) -> None:
    with (
        patch.object(service.settings, "notify_owner_on_completion", True),
        patch.object(service.settings, "smtp_host", "smtp.example.com"),
        patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send,
    ):
        await service.notify_owner_gallery_completed(email, {}, MagicMock())
    assert send.await_count == int(should_send)
    if should_send:
        assert send.await_args.kwargs["recipients"] == [email]


@pytest.mark.parametrize("email,should_send", [("one@example.com,two@example.com", False), ("one@example.com", True)])
async def test_configured_notification_requires_one_mailbox(email: str, should_send: bool) -> None:
    config = MagicMock()
    db = MagicMock()
    db.get = AsyncMock(return_value=MagicMock(email=email))
    db.commit = AsyncMock()
    with patch.object(service.aiosmtplib, "send", new=AsyncMock()) as send:
        await service._send_email(config, "signup_pending", {"email": "new@example.com", "admin_url": "x"}, db)
    assert send.await_count == int(should_send)
    if should_send:
        assert send.await_args.kwargs["recipients"] == [email]
    db.commit.assert_awaited_once()
