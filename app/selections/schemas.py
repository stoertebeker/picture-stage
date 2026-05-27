import uuid

from pydantic import BaseModel

from app.db.models import SelectionAction


class SelectionEventCreate(BaseModel):
    image_id: uuid.UUID
    action: SelectionAction
    comment: str | None = None


class SelectionState(BaseModel):
    image_id: uuid.UUID
    selected: bool
    favorited: bool
    comment: str | None


class SelectionSummary(BaseModel):
    total_images: int
    selected_count: int
    favorited_count: int
    commented_count: int
    selections: list[SelectionState]
