"""Per-user gallery quota enforcement.

Single source of truth for the "max galleries per user" rule so the API router
and the HTMX frontend stay in lockstep. The check runs only when a new gallery
is about to be created; it never touches existing galleries, so adjusting the
limit cannot strand a user (someone already above a lowered limit just cannot
create more until they drop below it).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Gallery


class GalleryQuotaExceeded(Exception):
    """Raised when a user already owns the maximum allowed number of galleries.

    Carries the active ``limit`` so each caller can render its own message
    (English API detail vs. localized frontend toast).
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Gallery limit reached ({limit})")


async def assert_within_gallery_quota(owner_id: uuid.UUID, db: AsyncSession) -> None:
    """Reject creation if the user is at or above their gallery limit.

    A configured limit of 0 (or below) means unlimited and skips the count
    query entirely.
    """
    limit = settings.max_galleries_per_user
    if limit <= 0:
        return

    count = await db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == owner_id))
    if (count or 0) >= limit:
        raise GalleryQuotaExceeded(limit)
