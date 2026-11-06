"""Bounded, owner-scoped queries shared by the HTML and API dashboards."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Gallery,
    Image,
    ImagePreview,
    PreviewVariant,
    SelectionAction,
    SelectionEvent,
    ShareSession,
)


@dataclass(frozen=True)
class DashboardGallery:
    gallery: Gallery
    image_count: int
    selected_count: int
    favorited_count: int
    commented_count: int
    last_activity: datetime | None
    cover_storage_key: str | None


async def load_dashboard_galleries(
    owner_id: uuid.UUID,
    db: AsyncSession,
    *,
    order_by: Literal["created_at", "updated_at"],
    include_covers: bool = False,
) -> list[DashboardGallery]:
    """Load dashboard summaries in four queries, plus one when covers are needed.

    Progress intentionally counts images that EVER received each positive action,
    including comments later cleared. CurrentSelection is a different contract.
    Aggregate each independent relation separately to avoid multiplying images,
    events and sessions, and materialize only one summary row per gallery.
    """
    ordering = Gallery.created_at if order_by == "created_at" else Gallery.updated_at
    galleries = list(
        (await db.scalars(select(Gallery).where(Gallery.owner_id == owner_id).order_by(ordering.desc()))).all()
    )
    if not galleries:
        return []

    image_counts = dict(
        (
            await db.execute(
                select(Image.gallery_id, func.count(Image.id))
                .join(Gallery, Gallery.id == Image.gallery_id)
                .where(Gallery.owner_id == owner_id)
                .group_by(Image.gallery_id)
            )
        )
        .tuples()
        .all()
    )
    selection_rows = await db.execute(
        select(
            Image.gallery_id,
            func.count(func.distinct(SelectionEvent.image_id)).filter(SelectionEvent.action == SelectionAction.select),
            func.count(func.distinct(SelectionEvent.image_id)).filter(
                SelectionEvent.action == SelectionAction.favorite
            ),
            func.count(func.distinct(SelectionEvent.image_id)).filter(SelectionEvent.action == SelectionAction.comment),
        )
        .select_from(SelectionEvent)
        .join(Image, SelectionEvent.image_id == Image.id)
        .join(Gallery, Gallery.id == Image.gallery_id)
        .where(Gallery.owner_id == owner_id)
        .group_by(Image.gallery_id)
    )
    selection_counts = {
        gallery_id: (selected, favorited, commented) for gallery_id, selected, favorited, commented in selection_rows
    }
    last_activity = dict(
        (
            await db.execute(
                select(ShareSession.gallery_id, func.max(ShareSession.started_at))
                .join(Gallery, Gallery.id == ShareSession.gallery_id)
                .where(Gallery.owner_id == owner_id)
                .group_by(ShareSession.gallery_id)
            )
        )
        .tuples()
        .all()
    )

    cover_keys: dict[uuid.UUID, str] = {}
    if include_covers:
        # Prefer any medium thumbnail over every small thumbnail, then choose
        # the first image within that variant. Original-only images are skipped.
        ranked_covers = (
            select(
                Image.gallery_id,
                ImagePreview.storage_key,
                func.row_number()
                .over(
                    partition_by=Image.gallery_id,
                    order_by=(
                        case((ImagePreview.variant == PreviewVariant.thumb_md, 0), else_=1),
                        Image.sort_order,
                        Image.id,
                    ),
                )
                .label("cover_rank"),
            )
            .select_from(ImagePreview)
            .join(Image, ImagePreview.image_id == Image.id)
            .join(Gallery, Gallery.id == Image.gallery_id)
            .where(
                Gallery.owner_id == owner_id,
                ImagePreview.variant.in_((PreviewVariant.thumb_md, PreviewVariant.thumb_sm)),
            )
            .subquery()
        )
        cover_keys = dict(
            (
                await db.execute(
                    select(ranked_covers.c.gallery_id, ranked_covers.c.storage_key).where(
                        ranked_covers.c.cover_rank == 1
                    )
                )
            )
            .tuples()
            .all()
        )

    return [
        DashboardGallery(
            gallery=gallery,
            image_count=image_counts.get(gallery.id, 0),
            selected_count=selection_counts.get(gallery.id, (0, 0, 0))[0],
            favorited_count=selection_counts.get(gallery.id, (0, 0, 0))[1],
            commented_count=selection_counts.get(gallery.id, (0, 0, 0))[2],
            last_activity=last_activity.get(gallery.id),
            cover_storage_key=cover_keys.get(gallery.id),
        )
        for gallery in galleries
    ]
