"""Queryable review state and indexed capture-date pagination.

Revision ID: 0018
Revises: 0017
"""

from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("images", sa.Column("capture_sort_key", sa.DateTime(), nullable=True))
    connection = op.get_bind()
    # Bounded migration batches, never a request-time repair or full ORM load.
    last_id = None
    while True:
        rows = connection.execute(
            sa.text(
                "SELECT id, exif FROM images WHERE (CAST(:last_id AS uuid) IS NULL "
                "OR id > CAST(:last_id AS uuid)) ORDER BY id LIMIT 500"
            ),
            {"last_id": str(last_id) if last_id else None},
        ).all()
        if not rows:
            break
        updates = []
        for image_id, exif in rows:
            raw = (exif.get("DateTimeOriginal") or exif.get("DateTime")) if exif else None
            value = datetime(9999, 1, 1)
            if isinstance(raw, str) and raw:
                try:
                    value = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    pass
            updates.append({"image_id": image_id, "value": value})
        connection.execute(sa.text("UPDATE images SET capture_sort_key = :value WHERE id = :image_id"), updates)
        last_id = rows[-1][0]
    op.alter_column("images", "capture_sort_key", nullable=False)
    for name, columns in (
        ("ix_images_gallery_order", ["gallery_id", "sort_order", "id"]),
        ("ix_images_gallery_filename", ["gallery_id", "filename", "id"]),
        ("ix_images_gallery_capture", ["gallery_id", "capture_sort_key", "sort_order", "id"]),
    ):
        op.create_index(name, "images", columns)
    op.create_table(
        "current_selections",
        sa.Column("image_id", sa.UUID(), sa.ForeignKey("images.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("favorited", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *[
            column
            for dimension in ("selected", "favorited", "comment")
            for column in (
                sa.Column(dimension + "_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column(dimension + "_event_id", sa.Integer(), nullable=True),
            )
        ],
    )
    # Each dimension uses exactly the same (created_at, id) ordering as live
    # writes. A transaction begun before waiting for a lock can be backdated.
    connection.execute(
        sa.text("""
        INSERT INTO current_selections
          (image_id, selected, favorited, comment, selected_at, selected_event_id,
           favorited_at, favorited_event_id, comment_at, comment_event_id)
        SELECT i.id, COALESCE(s.action = 'select', false), COALESCE(f.action = 'favorite', false), c.comment,
               s.created_at, s.id, f.created_at, f.id, c.created_at, c.id
        FROM images i
        LEFT JOIN LATERAL (
          SELECT action, created_at, id FROM selection_events WHERE image_id = i.id
          AND action IN ('select', 'deselect') ORDER BY created_at DESC, id DESC LIMIT 1
        ) s ON true
        LEFT JOIN LATERAL (
          SELECT action, created_at, id FROM selection_events WHERE image_id = i.id
          AND action IN ('favorite', 'unfavorite') ORDER BY created_at DESC, id DESC LIMIT 1
        ) f ON true
        LEFT JOIN LATERAL (
          SELECT comment, created_at, id FROM selection_events WHERE image_id = i.id
          AND action = 'comment' AND comment IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1
        ) c ON true
        WHERE s.id IS NOT NULL OR f.id IS NOT NULL OR c.id IS NOT NULL
    """)
    )


def downgrade() -> None:
    op.drop_table("current_selections")
    for name in ("ix_images_gallery_order", "ix_images_gallery_filename", "ix_images_gallery_capture"):
        op.drop_index(name, table_name="images")
    op.drop_column("images", "capture_sort_key")
