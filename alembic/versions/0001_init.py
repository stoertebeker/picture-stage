"""Initial schema creation.

Revision ID: 0001
Revises: 
Create Date: 2026-06-05

This migration creates all tables for Picture-Stage v0.1-v0.4.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create initial schema."""
    # User table
    op.create_table(
        "user",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    # Gallery table
    op.create_table(
        "gallery",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("share_token", sa.String(), nullable=True),
        sa.Column("share_token_hash", sa.String(), nullable=True),
        sa.Column("watermark_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("expiry_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_token_hash"),
    )
    op.create_index("ix_gallery_owner_id", "gallery", ["owner_id"])
    op.create_index("ix_gallery_share_token_hash", "gallery", ["share_token_hash"])

    # Image table
    op.create_table(
        "image",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["gallery.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_gallery_id", "image", ["gallery_id"])

    # ImagePreview table
    op.create_table(
        "image_preview",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("image_id", sa.UUID(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["image.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_preview_image_id", "image_preview", ["image_id"])

    # SelectionEvent table (append-only)
    op.create_table(
        "selection_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("image_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["gallery.id"], ),
        sa.ForeignKeyConstraint(["image_id"], ["image.id"], ),
        sa.ForeignKeyConstraint(["session_id"], ["share_session.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_selection_event_gallery_id", "selection_event", ["gallery_id"])
    op.create_index("ix_selection_event_image_id", "selection_event", ["image_id"])
    op.create_index("ix_selection_event_session_id", "selection_event", ["session_id"])

    # ShareSession table
    op.create_table(
        "share_session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["gallery.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_session_gallery_id", "share_session", ["gallery_id"])

    # AuditLog table
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("details", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["gallery.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_gallery_id", "audit_log", ["gallery_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    # NotificationConfig table
    op.create_table(
        "notification_config",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email_on_selection", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_notification_config_user_id", "notification_config", ["user_id"])

    # NotificationDelivery table
    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("config_id", sa.UUID(), nullable=False),
        sa.Column("gallery_id", sa.UUID(), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["notification_config.id"], ),
        sa.ForeignKeyConstraint(["gallery_id"], ["gallery.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_delivery_config_id", "notification_delivery", ["config_id"])
    op.create_index("ix_notification_delivery_gallery_id", "notification_delivery", ["gallery_id"])

    # PendingSignup table
    op.create_table(
        "pending_signup",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("email_verification_token", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("email_verification_token"),
    )
    op.create_index("ix_pending_signup_email", "pending_signup", ["email"])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_index("ix_pending_signup_email", table_name="pending_signup")
    op.drop_table("pending_signup")
    op.drop_index("ix_notification_delivery_gallery_id", table_name="notification_delivery")
    op.drop_index("ix_notification_delivery_config_id", table_name="notification_delivery")
    op.drop_table("notification_delivery")
    op.drop_index("ix_notification_config_user_id", table_name="notification_config")
    op.drop_table("notification_config")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_gallery_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_share_session_gallery_id", table_name="share_session")
    op.drop_table("share_session")
    op.drop_index("ix_selection_event_session_id", table_name="selection_event")
    op.drop_index("ix_selection_event_image_id", table_name="selection_event")
    op.drop_index("ix_selection_event_gallery_id", table_name="selection_event")
    op.drop_table("selection_event")
    op.drop_index("ix_image_preview_image_id", table_name="image_preview")
    op.drop_table("image_preview")
    op.drop_index("ix_image_gallery_id", table_name="image")
    op.drop_table("image")
    op.drop_index("ix_gallery_share_token_hash", table_name="gallery")
    op.drop_index("ix_gallery_owner_id", table_name="gallery")
    op.drop_table("gallery")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
