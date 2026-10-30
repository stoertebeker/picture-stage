from datetime import datetime
from typing import Any

from pydantic import BaseModel, HttpUrl, field_validator

from app.notifications.webhook import BLOCKED_HOSTS as BLOCKED_HOSTS
from app.notifications.webhook import parse_webhook_url

VALID_EVENT_TYPES = {"gallery_completed", "signup_pending"}


class NotificationConfigUpdate(BaseModel):
    email_enabled: bool = False
    webhook_url: str | None = None
    events: list[str]

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        invalid = set(v) - VALID_EVENT_TYPES
        if invalid:
            raise ValueError(f"Unknown event types: {', '.join(sorted(invalid))}")
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        HttpUrl(v)
        parse_webhook_url(v)
        return v


class NotificationConfigResponse(BaseModel):
    id: int
    email_enabled: bool
    webhook_url: str | None
    events: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class NotificationDeliveryResponse(BaseModel):
    id: int
    event: str
    status: str
    error_message: str | None
    attempted_at: datetime
