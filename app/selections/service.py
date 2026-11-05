import hashlib
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import CurrentSelection, Image, SelectionEvent
from app.selections.schemas import SelectionState


class SelectionHistoryFull(Exception):
    """The gallery has reached its retained selection event budget."""


async def lock_selection_budget(gallery_id: uuid.UUID, db: AsyncSession) -> None:
    """Serialize all writers for a gallery until the new event commits.

    Take the storage lifecycle gallery/image fences first. Existing events are
    never modified, including when the configured limit is lowered.
    """
    digest = hashlib.sha256(b"picture-stage:selection-budget:v1\0" + gallery_id.bytes).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with db.no_autoflush:
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def assert_selection_budget(gallery_id: uuid.UUID, db: AsyncSession) -> None:
    """Reject new events above the limit while holding the gallery budget lock."""
    with db.no_autoflush:
        count = await db.scalar(
            select(func.count(SelectionEvent.id))
            .join(Image, SelectionEvent.image_id == Image.id)
            .where(Image.gallery_id == gallery_id)
        )
    if (count or 0) >= settings.max_selection_events_per_gallery:
        raise SelectionHistoryFull


async def get_current_selections(gallery_id: uuid.UUID, db: AsyncSession) -> list[SelectionState]:
    """Read current state, including unrated images, without replaying history."""
    rows = await db.execute(
        select(Image.id, CurrentSelection.selected, CurrentSelection.favorited, CurrentSelection.comment)
        .outerjoin(CurrentSelection, CurrentSelection.image_id == Image.id)
        .where(Image.gallery_id == gallery_id)
        .order_by(Image.sort_order, Image.id)
    )
    return [
        SelectionState(image_id=row.id, selected=bool(row.selected), favorited=bool(row.favorited), comment=row.comment)
        for row in rows
    ]
