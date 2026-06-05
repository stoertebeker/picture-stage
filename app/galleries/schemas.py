import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.db.models import GalleryPhase, GalleryStatus

WatermarkPosition = Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"]


class WatermarkConfig(BaseModel):
    """Per-gallery watermark configuration. All fields optional; NULL means use global default."""

    text: str | None = Field(
        default=None,
        max_length=200,
        description="Watermark text. Supports {gallery_id} placeholder resolved at render time.",
    )
    position: WatermarkPosition | None = Field(
        default=None,
        description="Watermark position on the image.",
    )
    opacity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Watermark opacity (0.0 = invisible, 1.0 = fully opaque).",
    )
    font_size: int | None = Field(
        default=None,
        ge=10,
        le=200,
        description="Absolute font size in pixels. If not set, calculated from image width.",
    )


class GalleryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    watermark_config: dict[str, Any] | None = None
    expires_at: datetime | None = None

    @field_validator("watermark_config", mode="before")
    @classmethod
    def validate_watermark_config(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        if isinstance(v, dict):
            # Validate via WatermarkConfig, then return as dict (excluding unset fields)
            return WatermarkConfig(**v).model_dump(exclude_none=True)
        if isinstance(v, WatermarkConfig):
            return v.model_dump(exclude_none=True)
        msg = "watermark_config must be a dict or WatermarkConfig"
        raise ValueError(msg)


class GalleryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    watermark_config: dict[str, Any] | None = None
    expires_at: datetime | None = None

    @field_validator("watermark_config", mode="before")
    @classmethod
    def validate_watermark_config(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        if isinstance(v, dict):
            return WatermarkConfig(**v).model_dump(exclude_none=True)
        if isinstance(v, WatermarkConfig):
            return v.model_dump(exclude_none=True)
        msg = "watermark_config must be a dict or WatermarkConfig"
        raise ValueError(msg)


class GalleryStatusTransition(BaseModel):
    status: GalleryStatus


class GalleryResponse(BaseModel):
    id: uuid.UUID
    name: str
    phase: GalleryPhase
    status: GalleryStatus
    watermark_config: WatermarkConfig | dict[str, Any] | None
    expires_at: datetime | None
    has_share_token: bool
    image_count: int
    created_at: datetime
    updated_at: datetime


class GalleryListResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: GalleryStatus
    image_count: int
    expires_at: datetime | None
    created_at: datetime


class DashboardGalleryResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: GalleryStatus
    image_count: int
    selected_count: int
    favorited_count: int
    commented_count: int
    has_share_token: bool
    expires_at: datetime | None
    last_activity: datetime | None
    created_at: datetime


class DashboardResponse(BaseModel):
    galleries: list[DashboardGalleryResponse]
    total_galleries: int
    pending_signups_count: int | None = None


class GalleryDuplicateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


# --- Audit Log ---


class AuditLogEntry(BaseModel):
    id: int
    event_type: str
    actor_user_id: uuid.UUID | None
    actor_session_id: uuid.UUID | None
    ip_address: str | None
    user_agent: str | None
    details: dict[str, Any] | None
    created_at: datetime


class AuditLogResponse(BaseModel):
    entries: list[AuditLogEntry]
    total: int
    page: int
    per_page: int
    total_pages: int
