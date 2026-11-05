import csv
import io
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_active_user
from app.db.models import CurrentSelection, Gallery, Image, User
from app.db.session import get_db
from app.galleries.csv_security import spreadsheet_text

router = APIRouter(prefix="/api/v1/galleries", tags=["export"])


async def _get_materialized_selections(gallery_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    # The owner export must agree with guest state even for timestamp ties or
    # transactions that started before waiting for the selection write fence.
    rows = await db.execute(
        select(
            Image.id, Image.filename, CurrentSelection.selected, CurrentSelection.favorited, CurrentSelection.comment
        )
        .outerjoin(CurrentSelection, CurrentSelection.image_id == Image.id)
        .where(Image.gallery_id == gallery_id)
        .order_by(Image.sort_order, Image.id)
    )
    return [
        {
            "image_id": str(row.id),
            "filename": row.filename,
            "selected": bool(row.selected),
            "favorited": bool(row.favorited),
            "comment": row.comment,
        }
        for row in rows
    ]


@router.get("/{gallery_id}/export")
async def export_selections(
    gallery_id: uuid.UUID,
    format: str = Query("json", pattern="^(json|csv|txt)$"),
    filter: str = Query("all", pattern="^(all|selected|favorited|marked)$"),
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    result = await db.execute(select(Gallery).where(Gallery.id == gallery_id, Gallery.owner_id == user.id))
    gallery = result.scalar_one_or_none()
    if gallery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery not found")

    selections = await _get_materialized_selections(gallery_id, db)

    if filter == "selected":
        selections = [s for s in selections if s["selected"]]
    elif filter == "favorited":
        selections = [s for s in selections if s["favorited"]]
    elif filter == "marked":
        # Everything the model touched: a normal selection OR a favorite (r84).
        selections = [s for s in selections if s["selected"] or s["favorited"]]

    safe_name = gallery.name.replace(" ", "_").replace("/", "_")[:50]

    if format == "txt":
        # Comma-separated filenames (with extension) on a single line — the
        # format Lightroom Classic's filename filter and Capture One's
        # "Select by filename" expect for a paste-in selection (r84).
        body = ", ".join(s["filename"] for s in selections)
        return StreamingResponse(
            iter([body]),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_filenames.txt"'},
        )

    if format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["filename", "selected", "favorited", "comment"], quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for s in selections:
            writer.writerow(
                {
                    "filename": spreadsheet_text(s["filename"]),
                    "selected": s["selected"],
                    "favorited": s["favorited"],
                    "comment": spreadsheet_text(s["comment"] or ""),
                }
            )
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
