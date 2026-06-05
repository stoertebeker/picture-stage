"""Add 'disabled' value to the userstatus enum.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-05

Adds the ``disabled`` status so admins can lock a user out without deleting the
account. Mirrors ``UserStatus.disabled`` in app/db/models.py.
"""

from alembic import op

# Revision identifiers
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Append 'disabled' to the native PostgreSQL ``userstatus`` enum type.

    ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block on older
    PostgreSQL versions, so we use an autocommit block. ``IF NOT EXISTS`` keeps
    the migration idempotent if it is re-run.
    """
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'disabled'")


def downgrade() -> None:
    """No-op: PostgreSQL cannot drop a single enum value in place.

    Removing 'disabled' would require recreating the type and migrating every row
    that references it. We intentionally leave the value in place so downgrade
    stays safe and lossless.
    """
