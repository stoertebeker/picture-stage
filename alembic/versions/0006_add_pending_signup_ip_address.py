"""Add ip_address to pending_signups.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-13

Stores the client IP at signup time for abuse prevention (picture-stage-1qa).
Nullable so existing rows are unaffected; no backfill needed.
String(45) covers both IPv4 (max 15 chars) and IPv6 (max 45 chars).
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_signups",
        sa.Column("ip_address", sa.String(45), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_signups", "ip_address")
