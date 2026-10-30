"""Index replayable gallery tokens and locators for new verification tokens.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("galleries", sa.Column("share_token_lookup", sa.LargeBinary(), nullable=True))
    # Old installations may retain only salted hashes. Those links have no
    # recoverable plaintext and remain eligible for the bounded legacy scan.
    op.execute(
        "UPDATE galleries SET share_token_lookup = sha256(convert_to(share_token, 'UTF8')) "
        "WHERE share_token IS NOT NULL"
    )
    op.create_index("ix_galleries_share_token_lookup", "galleries", ["share_token_lookup"])
    op.add_column("pending_signups", sa.Column("verification_lookup_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "ix_pending_signups_verification_lookup_id", "pending_signups", ["verification_lookup_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_pending_signups_verification_lookup_id", table_name="pending_signups")
    op.drop_column("pending_signups", "verification_lookup_id")
    op.drop_index("ix_galleries_share_token_lookup", table_name="galleries")
    op.drop_column("galleries", "share_token_lookup")
