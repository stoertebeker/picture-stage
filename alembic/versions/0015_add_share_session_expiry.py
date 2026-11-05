"""Bound new share-session lifetimes without rewriting historical visits.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("share_sessions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_share_sessions_gallery_expiry",
        "share_sessions",
        ["gallery_id", "expires_at", "id"],
        postgresql_where=sa.text("completed_at IS NULL AND expires_at IS NOT NULL"),
    )
    op.create_index("ix_audit_log_actor_session_id", "audit_log", ["actor_session_id"])
    op.create_table(
        "share_session_cleanup_state",
        sa.Column("gallery_id", sa.UUID(), sa.ForeignKey("galleries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_session_id", sa.UUID(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("share_session_cleanup_state")
    op.drop_index("ix_audit_log_actor_session_id", table_name="audit_log")
    op.drop_index("ix_share_sessions_gallery_expiry", table_name="share_sessions")
    op.drop_column("share_sessions", "expires_at")
