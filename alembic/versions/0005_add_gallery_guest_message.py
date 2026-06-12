"""Add guest_message to galleries.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-12

Optional free-text note the photographer can attach to a gallery; it is shown to
the model in the guest viewer (picture-stage-dii). Nullable column, no default,
so existing galleries are unaffected and the deploy needs no backfill.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable galleries.guest_message column."""
    op.add_column("galleries", sa.Column("guest_message", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the galleries.guest_message column."""
    op.drop_column("galleries", "guest_message")
