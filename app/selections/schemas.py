import uuid

from pydantic import BaseModel, field_validator, model_validator

from app.config import settings
from app.db.models import SelectionAction


class SelectionEventCreate(BaseModel):
    image_id: uuid.UUID
    session_id: uuid.UUID
    action: SelectionAction
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def bounded_comment(cls, value: str | None) -> str | None:
        if value is not None and len(value) > settings.max_selection_comment_chars:
            raise ValueError("Comment exceeds the allowed length")
        return value

    @model_validator(mode="after")
    def comment_only_on_comment_action(self) -> "SelectionEventCreate":
        if self.action != SelectionAction.comment and self.comment is not None:
            raise ValueError("Comment is only allowed with the comment action")
        return self


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
