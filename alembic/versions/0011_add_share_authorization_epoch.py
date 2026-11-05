"""Bind guest sessions to a gallery authorization generation.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The shared zero backfill preserves existing sessions and their selection
    # history. Owner authorization changes advance the gallery generation later.
    op.add_column("galleries", sa.Column("authorization_epoch", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column(
        "share_sessions", sa.Column("authorization_epoch", sa.BigInteger(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("share_sessions", "authorization_epoch")
    op.drop_column("galleries", "authorization_epoch")
