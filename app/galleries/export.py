import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_active_user
from app.db.models import Gallery, Image, SelectionAction, SelectionEvent, User
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/galleries", tags=["export"])


async def _get_materialized_selections(
    gallery_id: uuid.UUID, db: AsyncSession
) -> list[dict]:
    result = await db.execute(
        select(Image).where(Image.gallery_id == gallery_id).order_by(Image.sort_order)
    )
    images = result.scalars().all()

    result = await db.execute(
        select(SelectionEvent)
        .where(SelectionEvent.image_id.in_([img.id for img in images]))
        .order_by(SelectionEvent.created_at)
    )
    events = result.scalars().all()

    state: dict[uuid.UUID, dict] = {}
    for img in images:
        state[img.id] = {
            "filename": img.filename,
            "selected": False,
            "favorited": False,
            "comment": None,
        }

    for event in events:
        s = state.get(event.image_id)
        if s is None:
            continue
        match event.action:
            case SelectionAction.select:
                s["selected"] = True
            case SelectionAction.deselect:
                s["selected"] = False
            case SelectionAction.favorite:
                s["favorited"] = True
            case SelectionAction.unfavorite:
                s["favorited"] = False
            case SelectionAction.comment:
                if event.comment is not None:
                    s["comment"] = event.comment

    return [{"image_id": str(img_id), **s} for img_id, s in state.items()]


@router.get("/{gallery_id}/export")
async def export_selections(
    gallery_id: uuid.UUID,
    format: str = Query("json", pattern="^(json|csv)$"),
    filter: str = Query("all", pattern="^(all|selected|favorited)$"),
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    result = await db.execute(
        select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id)
    )
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    selections = await _get_materialized_selections(gallery_id, db)

    if filter == "selected":
        selections = [s for s in selections if s["selected"]]
    elif filter == "favorited":
        selections = [s for s in selections if s["favorited"]]

    safe_name = gallery.name.replace(" ", "_").replace("/", "_")[:50]

    if format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["filename", "selected", "favorited", "comment"])
        writer.writeheader()
        for s in selections:
            writer.writerow({
                "filename": s["filename"],
                "selected": s["selected"],
                "favorited": s["favorited"],
                "comment": s["comment"] or "",
            })
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_selections.csv"'},
        )

    export_data = {
        "gallery": gallery.name,
        "gallery_id": str(gallery_id),
        "total_images": len(selections) if filter == "all" else None,
        "exported_count": len(selections),
        "filter": filter,
        "selections": [
            {
                "filename": s["filename"],
                "selected": s["selected"],
                "favorited": s["favorited"],
                "comment": s["comment"],
            }
            for s in selections
        ],
    }

    return StreamingResponse(
        iter([json.dumps(export_data, indent=2, ensure_ascii=False)]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_selections.json"'},
    )
