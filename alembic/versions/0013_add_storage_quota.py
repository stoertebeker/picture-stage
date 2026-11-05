"""Durable owner storage reservations and legacy cleanup ownership.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_usage",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("objects", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("unknown_sizes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("bytes >= 0 AND objects >= 0 AND unknown_sizes >= 0", name="ck_storage_usage_nonnegative"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_id"),
    )
    op.create_table(
        "storage_allocations",
        sa.Column("key", sa.String(1024), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gallery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("size IS NULL OR size >= 0", name="ck_storage_allocation_size"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("key"),
    )
    for column in ("owner_id", "gallery_id", "image_id"):
        op.create_index(f"ix_storage_allocations_{column}", "storage_allocations", [column])

    # Freeze source metadata throughout the one-time backfill. Never inspect,
    # rename or remove legacy backend objects during a schema migration.
    op.execute("LOCK TABLE galleries, images, image_previews, upload_cleanup_intents IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        INSERT INTO storage_allocations (key, owner_id, gallery_id, image_id, size)
        SELECT i.storage_key, g.owner_id, g.id, i.id,
               CASE WHEN i.file_size >= 0 THEN i.file_size ELSE NULL END
        FROM images i JOIN galleries g ON g.id = i.gallery_id
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute("""
        INSERT INTO storage_allocations (key, owner_id, gallery_id, image_id, size)
        SELECT p.storage_key, g.owner_id, g.id, i.id,
               CASE WHEN p.file_size >= 0 THEN p.file_size ELSE NULL END
        FROM image_previews p JOIN images i ON i.id = p.image_id
        JOIN galleries g ON g.id = i.gallery_id
        ON CONFLICT (key) DO NOTHING
    """)
    # An old failed worker can have written canonical derivatives without
    # committed preview rows. Their size cannot safely be inferred as zero.
    op.execute("""
        INSERT INTO storage_allocations (key, owner_id, gallery_id, image_id, size)
        SELECT g.id::text || '/previews/' || i.id::text || '_' || v.name || '.webp',
               g.owner_id, g.id, i.id, NULL
        FROM images i JOIN galleries g ON g.id = i.gallery_id
        CROSS JOIN (VALUES ('thumb_sm'), ('thumb_md'), ('preview')) AS v(name)
        WHERE i.processing_status != 'ready'
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute("""
        INSERT INTO storage_allocations (key, owner_id, gallery_id, image_id, size)
        SELECT k.value, g.owner_id, g.id, e.key::uuid, NULL
        FROM upload_cleanup_intents u JOIN galleries g ON g.id = u.gallery_id
        CROSS JOIN LATERAL json_each(u.image_keys) e
        CROSS JOIN LATERAL json_array_elements_text(e.value) k
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute("""
        INSERT INTO storage_usage (owner_id, bytes, objects, unknown_sizes)
        SELECT owner_id, COALESCE(SUM(size), 0), COUNT(*), COUNT(*) FILTER (WHERE size IS NULL)
        FROM storage_allocations GROUP BY owner_id
    """)


def downgrade() -> None:
    op.drop_table("storage_allocations")
    op.drop_table("storage_usage")
