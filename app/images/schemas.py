import uuid
from datetime import datetime

from pydantic import BaseModel


class ImageResponse(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    width: int | None
    height: int | None
    file_size: int | None
    sort_order: int
    created_at: datetime
    previews: dict[str, str]

    model_config = {"from_attributes": True}


class ImageUploadResponse(BaseModel):
    uploaded: int
    images: list[ImageResponse]
