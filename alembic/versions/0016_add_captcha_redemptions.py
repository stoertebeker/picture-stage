"""Persist one-time signup challenge redemption across workers.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "captcha_redemptions",
        sa.Column("signature", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_captcha_redemptions_expires_at", "captcha_redemptions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("captcha_redemptions")
