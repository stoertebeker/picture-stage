"""Index bounded legacy verification candidate windows.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_pending_signups_legacy_token_candidates",
        "pending_signups",
        ["id"],
        postgresql_where=sa.text("verification_lookup_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_pending_signups_legacy_token_candidates", table_name="pending_signups")
