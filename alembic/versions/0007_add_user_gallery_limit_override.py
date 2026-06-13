"""Add gallery_limit_override to users.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-13

Per-user override for the global MAX_GALLERIES_PER_USER default
(picture-stage-56k). NULL = use global setting; 0 = unlimited;
positive = specific cap. Nullable so existing users default to the
global limit without any backfill.
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("gallery_limit_override", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "gallery_limit_override")
