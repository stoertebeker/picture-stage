import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import aiosmtplib
import httpx
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import NotificationConfig, NotificationDelivery, User, UserStatus

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)

SUBJECT_MAP = {
    "gallery_completed": "Bewertung abgeschlossen - {{ gallery_name }}",
    "signup_pending": "Neue Registrierung - {{ email }}",
    "verify_email": "E-Mail-Adresse bestätigen - Picture-Stage",
}


async def send_notification(
    event_type: str,
    user_id: str,
    payload: dict[str, Any],
    db: AsyncSession,
) -> None:
    result = await db.execute(select(NotificationConfig).where(NotificationConfig.user_id == user_id))
    configs = result.scalars().all()

    for config in configs:
        subscribed_events = config.events if isinstance(config.events, dict) else {}
        if not subscribed_events.get(event_type, False):
            continue

        if config.email_enabled and settings.smtp_host:
            await _send_email(config, event_type, payload, db)

        if config.webhook_url:
            await _send_webhook(config, event_type, payload, db)


async def notify_all_admins(
    event_type: str,
    payload: dict[str, Any],
    db: AsyncSession,
) -> None:
    result = await db.execute(select(User.id).where(User.status == UserStatus.admin))
    admin_ids = [row[0] for row in result.all()]

    for admin_id in admin_ids:
        await send_notification(event_type, str(admin_id), payload, db)


async def notify_admins_signup(pending_email: str, db: AsyncSession) -> None:
    """Email every admin that a new user registered — guaranteed operational alert.

    Unlike notify_all_admins(), this bypasses the per-user NotificationConfig
    opt-in: there is no UI to configure it, so a config-gated path would silently
    deliver nothing. Gated only by the notify_admins_on_signup setting and a
    configured SMTP host. Failures are logged per-recipient and never propagate
    to the caller — a signup must succeed even if mail delivery is down.
    """
    if not settings.notify_admins_on_signup or not settings.smtp_host:
        return

    result = await db.execute(select(User.email).where(User.status == UserStatus.admin))
    admin_emails = [row[0] for row in result.all()]
    if not admin_emails:
        return

    payload = {"email": pending_email, "admin_url": f"{settings.app_url}/admin/users"}
    try:
        html_body = _jinja_env.get_template("signup_pending.html").render(**payload)
        text_body = _jinja_env.get_template("signup_pending.txt").render(**payload)
        subject = _jinja_env.from_string(SUBJECT_MAP["signup_pending"]).render(**payload)
    except Exception:
        logger.exception("Failed to render signup_pending templates")
        return

    for admin_email in admin_emails:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = settings.smtp_from
            msg["To"] = admin_email
            msg["Subject"] = subject
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_password or None,
                start_tls=settings.smtp_starttls,
            )
        except Exception as exc:
            logger.warning("signup_pending notification to admin failed: %s", exc)


async def send_verification_email(email: str, token: str, db: AsyncSession) -> None:
    """Email a freshly registered user a link to confirm their address.

    Config-free like notify_admins_signup(): there is no per-user opt-in UI at
    signup time, so this bypasses NotificationConfig and is gated only by the
    send_verification_email_enabled setting plus a configured SMTP host. The
    verification link is built from app_url (the HTTPS source of truth) and
    carries the *plaintext* token — the DB stores only its SHA-256+salt hash.
    Failures are logged and never propagate: a signup must succeed even when
    mail delivery is down. Callers pass the token from the same neutral
    new-signup path only, so this never fires for an already-registered email
    (keeps the account-enumeration guard, picture-stage-42q, intact).

    db is accepted for signature symmetry with the other notify helpers and to
    allow future delivery tracking; it is currently unused.
    """
    if not settings.send_verification_email_enabled or not settings.smtp_host:
        return

    payload = {"verify_url": f"{settings.app_url}/verify-email/{token}"}
    try:
        html_body = _jinja_env.get_template("verify_email.html").render(**payload)
        text_body = _jinja_env.get_template("verify_email.txt").render(**payload)
        subject = _jinja_env.from_string(SUBJECT_MAP["verify_email"]).render(**payload)
    except Exception:
        logger.exception("Failed to render verify_email templates")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = settings.smtp_from
        msg["To"] = email
        msg["Subject"] = subject
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
        )
    except Exception as exc:
        logger.warning("verification email to %s failed: %s", email, exc)


async def _send_email(
    config: NotificationConfig,
    event_type: str,
    payload: dict[str, Any],
    db: AsyncSession,
) -> None:
    delivery = NotificationDelivery(
        config_id=config.id,
        event=event_type,
        status="pending",
    )
    db.add(delivery)

    try:
        html_template = _jinja_env.get_template(f"{event_type}.html")
        text_template = _jinja_env.get_template(f"{event_type}.txt")
        html_body = html_template.render(**payload)
        text_body = text_template.render(**payload)

        subject_tpl = SUBJECT_MAP.get(event_type, event_type)
        subject = _jinja_env.from_string(subject_tpl).render(**payload)

        user = await db.get(User, config.user_id)
        if user is None:
            delivery.status = "failed"
            delivery.error_message = "User not found"
            await db.commit()
            return

        msg = MIMEMultipart("alternative")
        msg["From"] = settings.smtp_from
        msg["To"] = user.email
        msg["Subject"] = subject
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
        )

        delivery.status = "sent"
    except Exception as exc:
        logger.warning("Email notification failed for config %s: %s", config.id, exc)
        delivery.status = "failed"
        delivery.error_message = str(exc)[:500]

    await db.commit()


async def _send_webhook(
    config: NotificationConfig,
    event_type: str,
    payload: dict[str, Any],
    db: AsyncSession,
) -> None:
    delivery = NotificationDelivery(
        config_id=config.id,
        event=event_type,
        status="pending",
    )
    db.add(delivery)

    webhook_url = config.webhook_url
    if webhook_url is None:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json={"event": event_type, "data": payload},
                headers={"Content-Type": "application/json", "User-Agent": "Picture-Stage/0.1"},
            )
            response.raise_for_status()
        delivery.status = "sent"
    except Exception as exc:
        logger.warning("Webhook notification failed for config %s: %s", config.id, exc)
        delivery.status = "failed"
        delivery.error_message = str(exc)[:500]

    await db.commit()
