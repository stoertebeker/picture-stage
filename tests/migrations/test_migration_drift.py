"""Schema-drift guard: the Alembic migrations must produce exactly the schema
the ORM models declare. Runs against real PostgreSQL in CI.

This closes the gap left by the integration suite, which builds its schema with
``Base.metadata.create_all`` and therefore never exercises the migrations. Three
separate drifts slipped through that gap historically (table ordering, a silent
migration skip, and VARCHAR columns where the ORM expected native ENUM types).
Here we run the *real* migration path and diff the result against the ORM
metadata, so any future divergence fails CI immediately.

Requires a live PostgreSQL via DATABASE_URL (the CI postgres service). It will
not run inside the sandbox (loopback to the DB is blocked).
"""

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import models  # noqa: F401 — registers every table on Base.metadata
from app.db.base import Base
from app.db.migrations import run_migrations

# Native ENUM types created by migration 0001. They are not owned by any table's
# DROP, so we drop them explicitly to guarantee a truly empty starting schema.
_ENUM_TYPES = ("userstatus", "galleryphase", "gallerystatus", "previewvariant", "selectionaction")

# compare_metadata diff op-codes to tolerate. Only server_default differences are
# ignored: a handful of columns carry a DB-side default in the migration that the
# ORM sets Python-side only. That is harmless, not drift. Everything else —
# missing/extra tables or columns, wrong types (the ENUM bug), nullability,
# constraints, indexes — is treated as a failure.
_IGNORED_OPS = {"modify_default"}


async def _drop_everything(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        for name in _ENUM_TYPES:
            await conn.exec_driver_sql(f"DROP TYPE IF EXISTS {name}")


def _significant_diffs(diffs: list[Any]) -> list[Any]:
    """Flatten compare_metadata output and drop tolerated (server_default) ops."""
    flat: list[Any] = []
    for entry in diffs:
        # Column-level changes arrive as a list of (op, ...) tuples; table-level
        # ones as a single tuple.
        items = entry if isinstance(entry, list) else [entry]
        for item in items:
            if item[0] not in _IGNORED_OPS:
                flat.append(item)
    return flat


async def test_migrations_match_orm_models() -> None:
    """Running the migrations from empty must reproduce the ORM schema exactly."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        await _drop_everything(engine)
        await run_migrations()  # the real path: alembic command.upgrade("head")

        def _diff(sync_conn: Connection) -> list[Any]:
            ctx = MigrationContext.configure(sync_conn)
            return list(compare_metadata(ctx, Base.metadata))

        async with engine.connect() as conn:
            diffs = await conn.run_sync(_diff)
    finally:
        await _drop_everything(engine)
        await engine.dispose()

    significant = _significant_diffs(diffs)
    assert not significant, "Alembic migrations drifted from the ORM models:\n" + "\n".join(
        repr(d) for d in significant
    )
