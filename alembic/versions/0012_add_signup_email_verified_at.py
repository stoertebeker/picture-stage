"""Record explicit email verification for pending signups.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical empty token verifiers do not prove a verification event or its
    # time. Leave every existing signup unverified until explicit verification.
    op.add_column("pending_signups", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("pending_signups", "email_verified_at")
