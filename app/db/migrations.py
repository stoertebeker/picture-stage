"""Database migration utilities."""

import logging

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings

logger = logging.getLogger(__name__)

_ALEMBIC_VERSION_DDL = sa.text(
    "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
)
_STAMP_SQL = sa.text(
    "INSERT INTO alembic_version (version_num) VALUES (:v) ON CONFLICT (version_num) DO UPDATE SET version_num = :v"
)


async def run_migrations() -> None:
    """Run pending Alembic migrations at app startup."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    configuration = cfg.get_section(cfg.config_ini_section, {})
    engine = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with engine.connect() as conn:
        await conn.run_sync(_run_sync, cfg)

    await engine.dispose()
    logger.info("Migrations complete")


def _run_sync(conn, cfg: Config) -> None:  # type: ignore[no-untyped-def]
    script = ScriptDirectory.from_config(cfg)
    mc = MigrationContext.configure(conn)

    current = mc.get_current_revision()
    heads = script.get_heads()
    if not heads or current == heads[0]:
        logger.info("Already up to date (%s)", current)
        return

    target = heads[0]
    revisions = list(reversed(list(script.walk_revisions(head=target, base=current or "base"))))
    logger.info("Migrating %s → %s (%d step(s))", current or "base", target, len(revisions))

    with mc.begin_transaction():
        conn.execute(_ALEMBIC_VERSION_DDL)
        with Operations.context(mc):
            for rev in revisions:
                logger.info("Applying %s", rev.revision)
                rev.module.upgrade()
        conn.execute(_STAMP_SQL, {"v": target})

    logger.info("Stamped %s", target)
