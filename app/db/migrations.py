"""Database migration utilities."""

import logging

from alembic.config import Config
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings

logger = logging.getLogger(__name__)


async def run_migrations() -> None:
    """Run Alembic migrations asynchronously at app startup."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    configuration = cfg.get_section(cfg.config_ini_section, {})
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_migrations, cfg)

    await connectable.dispose()
    logger.info("Database migrations completed successfully")


def _do_migrations(connection, cfg: Config) -> None:  # type: ignore[no-untyped-def]
    """Execute migrations synchronously (called via run_sync)."""
    from alembic import command

    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")
