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

    # Use async engine for migrations
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
    from alembic.runtime.migration import MigrationContext

    ctx = MigrationContext.configure(connection)

    # Import all models to register them
    from app.db import models as _  # noqa: F401

    # Run pending migrations
    with ctx.begin_transaction():
        # Get the current revision
        current_rev = ctx.get_current_revision()
        logger.debug(f"Current revision: {current_rev}")

        # Import and run migrations
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()

        if not heads:
            logger.warning("No migration revisions found")
            return

        target_rev = heads[0]
        base_rev = current_rev or "base"

        logger.info(f"Running migrations from {base_rev} to {target_rev}")

        for revision in script.walk_revisions(head=target_rev, base=base_rev):
            logger.info(f"Applying migration {revision.revision}")
            # Call the upgrade function from the migration module
            revision.module.upgrade()
