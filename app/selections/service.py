import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Image, SelectionAction, SelectionEvent
from app.selections.schemas import SelectionState


async def get_current_selections(
    gallery_id: uuid.UUID, session_id: uuid.UUID, db: AsyncSession
) -> list[SelectionState]:
    result = await db.execute(
        select(Image.id).where(Image.gallery_id == gallery_id).order_by(Image.sort_order)
    )
    image_ids = [row[0] for row in result.all()]

    result = await db.execute(
        select(SelectionEvent)
        .where(
            SelectionEvent.share_session_id == session_id,
            SelectionEvent.image_id.in_(image_ids),
        )
        .order_by(SelectionEvent.created_at)
    )
    events = result.scalars().all()

    state: dict[uuid.UUID, dict] = {
        img_id: {"selected": False, "favorited": False, "comment": None}
        for img_id in image_ids
    }

    for event in events:
        img = state.get(event.image_id)
        if img is None:
            continue

        match event.action:
            case SelectionAction.select:
                img["selected"] = True
            case SelectionAction.deselect:
                img["selected"] = False
            case SelectionAction.favorite:
                img["favorited"] = True
            case SelectionAction.unfavorite:
                img["favorited"] = False
            case SelectionAction.comment:
                if event.comment is not None:
                    img["comment"] = event.comment

    return [
        SelectionState(image_id=img_id, **s)
        for img_id, s in state.items()
    ]
