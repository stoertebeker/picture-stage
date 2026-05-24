import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import GalleryPhase, GalleryStatus


class GalleryCreate(BaseModel):
    name: str
    watermark_config: dict | None = None


class GalleryUpdate(BaseModel):
    name: str | None = None
    status: GalleryStatus | None = None
    watermark_config: dict | None = None
    expires_at: datetime | None = None


class GalleryResponse(BaseModel):
    id: uuid.UUID
    name: str
    phase: GalleryPhase
    status: GalleryStatus
    watermark_config: dict | None
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
    created_at: datetime
