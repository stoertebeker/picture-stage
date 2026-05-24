import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.auth.passwords import hash_password
from app.config import settings
from app.db.base import async_session
from app.db.models import User, UserStatus

logger = logging.getLogger(__name__)


async def create_initial_admin() -> None:
    if not settings.admin_email or not settings.admin_password:
        return

    async with async_session() as db:
        result = await db.execute(select(func.count()).select_from(User))
        user_count = result.scalar() or 0

        if user_count > 0:
            return

        admin = User(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            status=UserStatus.admin,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(admin)
        await db.commit()
        logger.info("Initial admin account created: %s", settings.admin_email)
