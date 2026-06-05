"""Database migration utilities."""

import logging

from alembic.config import Config
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import command
from app.config import settings

logger = logging.getLogger(__name__)


async def run_migrations() -> None:
    """Run pending Alembic migrations at app startup.

    Drives Alembic's own ``command.upgrade`` through a shared async connection
    (the official async recipe). Alembic owns the ``alembic_version`` table and
    the migration transaction, so the recorded revision and the actual schema
    can never drift apart — the failure mode where ``alembic_version`` is
    stamped but the tables are missing is structurally impossible here.
    """
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    configuration = cfg.get_section(cfg.config_ini_section, {})
    engine = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        # connect() (not begin()): env.py owns the transaction via
        # context.begin_transaction(), avoiding a nested transaction on PG.
        async with engine.connect() as conn:
            await conn.run_sync(_upgrade_to_head, cfg)
    finally:
        await engine.dispose()
    logger.info("Migrations complete")


def _upgrade_to_head(connection: Connection, cfg: Config) -> None:
    """Run inside run_sync: hand the live connection to Alembic and upgrade."""
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")
