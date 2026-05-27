from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_active_user
from app.db.models import NotificationConfig, NotificationDelivery, User
from app.db.session import get_db
from app.notifications.schemas import (
    NotificationConfigResponse,
    NotificationConfigUpdate,
    NotificationDeliveryResponse,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("/config", response_model=NotificationConfigResponse | None)
async def get_notification_config(
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationConfig | None:
    result = await db.execute(
        select(NotificationConfig).where(NotificationConfig.user_id == user.id)
    )
    return result.scalar_one_or_none()


@router.put("/config", response_model=NotificationConfigResponse)
async def upsert_notification_config(
    body: NotificationConfigUpdate,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationConfig:
    result = await db.execute(
        select(NotificationConfig).where(NotificationConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()

    events_dict = {e: True for e in body.events}

    if config is None:
        config = NotificationConfig(
            user_id=user.id,
            email_enabled=body.email_enabled,
            webhook_url=body.webhook_url,
            events=events_dict,
        )
        db.add(config)
    else:
        config.email_enabled = body.email_enabled
        config.webhook_url = body.webhook_url
        config.events = events_dict

    await db.commit()
    await db.refresh(config)
    return config


@router.get("/deliveries", response_model=list[NotificationDeliveryResponse])
async def list_deliveries(
    limit: int = 50,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationDelivery]:
    clamped_limit = min(max(limit, 1), 200)
    result = await db.execute(
        select(NotificationDelivery)
        .join(NotificationConfig, NotificationDelivery.config_id == NotificationConfig.id)
        .where(NotificationConfig.user_id == user.id)
        .order_by(NotificationDelivery.attempted_at.desc())
        .limit(clamped_limit)
    )
    return list(result.scalars().all())
