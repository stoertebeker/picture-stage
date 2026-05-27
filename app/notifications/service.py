import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

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
}


async def send_notification(
    event_type: str,
    user_id: str,
    payload: dict,
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
    payload: dict,
    db: AsyncSession,
) -> None:
    result = await db.execute(select(User.id).where(User.status == UserStatus.admin))
    admin_ids = [row[0] for row in result.all()]

    for admin_id in admin_ids:
        await send_notification(event_type, str(admin_id), payload, db)


async def _send_email(
    config: NotificationConfig,
    event_type: str,
    payload: dict,
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
    payload: dict,
    db: AsyncSession,
) -> None:
    delivery = NotificationDelivery(
        config_id=config.id,
        event=event_type,
        status="pending",
    )
    db.add(delivery)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                config.webhook_url,
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
