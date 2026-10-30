"""Keep durable upload storage ownership until the image batch commits.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_cleanup_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gallery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_keys", postgresql.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_cleanup_intents_gallery_id", "upload_cleanup_intents", ["gallery_id"])


def downgrade() -> None:
    op.drop_index("ix_upload_cleanup_intents_gallery_id", table_name="upload_cleanup_intents")
    op.drop_table("upload_cleanup_intents")
