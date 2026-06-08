"""Add processing_status to images for async preview generation.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-08

Preview generation moves from synchronous (inline in the upload request) to a
background worker. ``images.processing_status`` tracks the lifecycle so the grid
can show a spinner (pending), the thumbnail (ready), or an error tile (failed).

Existing images already have previews, so they are backfilled to ``ready`` via a
server_default that is dropped afterwards. New rows get ``pending`` from the ORM
default (app/db/models.py: ImageProcessingStatus). Mirrors the explicit-enum
pattern from 0001_init.py.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Native PostgreSQL ENUM, mirroring ImageProcessingStatus in app/db/models.py.
# create_type=False: created/dropped explicitly so add_column does not emit a
# second CREATE TYPE.
image_processing_status = postgresql.ENUM("pending", "ready", "failed", name="imageprocessingstatus", create_type=False)


def upgrade() -> None:
    """Add the enum type and the images.processing_status column.

    Backfill strategy: existing rows have previews -> ``ready``. We add the column
    with server_default='ready' (NOT NULL, no table rewrite of existing data into
    'pending'), then drop the server_default so future inserts rely on the ORM
    default of 'pending'. Prevents old galleries from showing as 'processing'.
    """
    bind = op.get_bind()
    image_processing_status.create(bind, checkfirst=True)

    op.add_column(
        "images",
        sa.Column(
            "processing_status",
            image_processing_status,
            nullable=False,
            server_default="ready",
        ),
    )
    # New rows should come from the ORM default ('pending'), not the DB default.
    op.alter_column("images", "processing_status", server_default=None)


def downgrade() -> None:
    """Drop the column and the enum type."""
    op.drop_column("images", "processing_status")
    image_processing_status.drop(op.get_bind(), checkfirst=True)
