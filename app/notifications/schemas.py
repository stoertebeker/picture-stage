from datetime import datetime

from pydantic import BaseModel, HttpUrl, field_validator

VALID_EVENT_TYPES = {"gallery_completed", "signup_pending"}

BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "metadata.google.internal", "169.254.169.254"}


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
        url = HttpUrl(v)
        host = (url.host or "").lower()
        if host in BLOCKED_HOSTS or host.endswith(".internal") or host.endswith(".local"):
            raise ValueError("Webhook URL must not point to internal addresses")
        if url.scheme not in ("https",):
            raise ValueError("Webhook URL must use HTTPS")
        return v


class NotificationConfigResponse(BaseModel):
    id: int
    email_enabled: bool
    webhook_url: str | None
    events: dict
    created_at: datetime
    updated_at: datetime


class NotificationDeliveryResponse(BaseModel):
    id: int
    event: str
    status: str
    error_message: str | None
    attempted_at: datetime
