"""Initial schema creation.

Revision ID: 0001
Revises:
Create Date: 2026-06-05

This migration creates all tables for Picture-Stage v0.1-v0.4.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Native PostgreSQL ENUM types, mirroring the StrEnums in app/db/models.py.
# create_type=False: we create/drop them explicitly (idempotent) so create_table
# does not try to emit a second CREATE TYPE.
user_status = postgresql.ENUM("pending", "active", "admin", name="userstatus", create_type=False)
gallery_phase = postgresql.ENUM("review", name="galleryphase", create_type=False)
gallery_status = postgresql.ENUM("draft", "shared", "completed", "archived", name="gallerystatus", create_type=False)
preview_variant = postgresql.ENUM("thumb_sm", "thumb_md", "preview", name="previewvariant", create_type=False)
selection_action = postgresql.ENUM(
    "select", "deselect", "favorite", "unfavorite", "comment", name="selectionaction", create_type=False
)

_ENUM_TYPES = (user_status, gallery_phase, gallery_status, preview_variant, selection_action)


def upgrade() -> None:
    """Create initial schema."""
    bind = op.get_bind()
    for enum_type in _ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("status", user_status, nullable=False, server_default="pending"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locale", sa.String(10), nullable=False, server_default="de"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    # Email uniqueness is enforced by the unique index (matches the ORM, which
    # declares unique=True + index=True -> a single unique index, no constraint).
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # galleries
    op.create_table(
        "galleries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("phase", gallery_phase, nullable=False, server_default="review"),
        sa.Column("status", gallery_status, nullable=False, server_default="draft"),
        sa.Column("share_token_hash", sa.LargeBinary(), nullable=True),
        sa.Column("share_token_salt", sa.LargeBinary(), nullable=True),
        sa.Column("share_token", sa.String(128), nullable=True),
        sa.Column("password_hash", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_galleries_owner_id", "galleries", ["owner_id"])

    # images
    op.create_table(
        "images",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False, server_default="image/jpeg"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("exif", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_images_gallery_id", "images", ["gallery_id"])

    # image_previews
    op.create_table(
        "image_previews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", sa.UUID(), nullable=False),
        sa.Column("variant", preview_variant, nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_previews_image_id", "image_previews", ["image_id"])
    op.create_index("ix_preview_image_variant", "image_previews", ["image_id", "variant"], unique=True)

    # share_sessions (must precede selection_events which FK-references it)
    op.create_table(
        "share_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_sessions_gallery_id", "share_sessions", ["gallery_id"])

    # selection_events (append-only)
    op.create_table(
        "selection_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", sa.UUID(), nullable=False),
        sa.Column("share_session_id", sa.UUID(), nullable=False),
        sa.Column("action", selection_action, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_session_id"], ["share_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_selection_events_image_id", "selection_events", ["image_id"])
    op.create_index("ix_selection_events_share_session_id", "selection_events", ["share_session_id"])
    op.create_index("ix_selection_events_created_at", "selection_events", ["created_at"])

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_session_id", sa.UUID(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("details", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_gallery_id", "audit_log", ["gallery_id"])
    op.create_index("ix_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    # notification_configs
    op.create_table(
        "notification_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("webhook_url", sa.String(2048), nullable=True),
        sa.Column("events", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_configs_user_id", "notification_configs", ["user_id"])

    # notification_deliveries
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["config_id"], ["notification_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_deliveries_config_id", "notification_deliveries", ["config_id"])

    # pending_signups
    op.create_table(
        "pending_signups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("verification_token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("verification_token_salt", sa.LargeBinary(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_signups_email", "pending_signups", ["email"], unique=True)


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_index("ix_pending_signups_email", table_name="pending_signups")
    op.drop_table("pending_signups")

    op.drop_index("ix_notification_deliveries_config_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")

    op.drop_index("ix_notification_configs_user_id", table_name="notification_configs")
    op.drop_table("notification_configs")

    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_event_type", table_name="audit_log")
    op.drop_index("ix_audit_log_gallery_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_selection_events_created_at", table_name="selection_events")
    op.drop_index("ix_selection_events_share_session_id", table_name="selection_events")
    op.drop_index("ix_selection_events_image_id", table_name="selection_events")
    op.drop_table("selection_events")

    op.drop_index("ix_share_sessions_gallery_id", table_name="share_sessions")
    op.drop_table("share_sessions")

    op.drop_index("ix_preview_image_variant", table_name="image_previews")
    op.drop_index("ix_image_previews_image_id", table_name="image_previews")
    op.drop_table("image_previews")

    op.drop_index("ix_images_gallery_id", table_name="images")
    op.drop_table("images")

    op.drop_index("ix_galleries_owner_id", table_name="galleries")
    op.drop_table("galleries")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    # Drop the ENUM types last — tables referencing them are already gone.
    bind = op.get_bind()
    for enum_type in reversed(_ENUM_TYPES):
        enum_type.drop(bind, checkfirst=True)
