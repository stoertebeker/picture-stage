import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, generate_uuid

# --- Enums ---


class UserStatus(enum.StrEnum):
    pending = "pending"
    active = "active"
    admin = "admin"
    disabled = "disabled"


# Single source of truth for which user statuses may authenticate / hold a session.
# Any status NOT in this set (pending, disabled) is denied access at login and on
# every protected route. Centralised so a newly added status can never silently
# "leak" access by being forgotten in one of the auth checks.
LOGIN_ALLOWED_STATUSES = frozenset({UserStatus.active, UserStatus.admin})


class GalleryPhase(enum.StrEnum):
    review = "review"


class GalleryStatus(enum.StrEnum):
    draft = "draft"
    shared = "shared"
    completed = "completed"
    archived = "archived"


class PreviewVariant(enum.StrEnum):
    thumb_sm = "thumb_sm"
    thumb_md = "thumb_md"
    preview = "preview"


class ImageProcessingStatus(enum.StrEnum):
    """Lifecycle of an image's preview generation.

    pending: original stored, previews not yet generated (background worker queued).
    ready:   all preview variants generated and available.
    failed:  preview generation raised; the original is stored but unusable previews.
    """

    pending = "pending"
    ready = "ready"
    failed = "failed"


class SelectionAction(enum.StrEnum):
    select = "select"
    deselect = "deselect"
    favorite = "favorite"
    unfavorite = "unfavorite"
    comment = "comment"


# --- Models ---


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), nullable=False, default=UserStatus.pending)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="de")
    # Access tokens issued before this instant are rejected (see app/auth/dependencies.py).
    # Set to now() on admin password-reset or account-lock to invalidate stateless JWTs
    # immediately. NULL = no invalidation point, so existing tokens stay valid.
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    galleries: Mapped[list["Gallery"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    notification_configs: Mapped[list["NotificationConfig"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Gallery(TimestampMixin, Base):
    __tablename__ = "galleries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional free-text note from the photographer, shown to the model in the guest viewer.
    guest_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[GalleryPhase] = mapped_column(Enum(GalleryPhase), nullable=False, default=GalleryPhase.review)
    status: Mapped[GalleryStatus] = mapped_column(Enum(GalleryStatus), nullable=False, default=GalleryStatus.draft)

    share_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    share_token_salt: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    share_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    watermark_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    owner: Mapped["User"] = relationship(back_populates="galleries")
    images: Mapped[list["Image"]] = relationship(back_populates="gallery", cascade="all, delete-orphan")
    share_sessions: Mapped[list["ShareSession"]] = relationship(back_populates="gallery", cascade="all, delete-orphan")
    audit_entries: Mapped[list["AuditLog"]] = relationship(back_populates="gallery", cascade="save-update, merge")


class Image(TimestampMixin, Base):
    __tablename__ = "images"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    gallery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="image/jpeg")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exif: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_status: Mapped[ImageProcessingStatus] = mapped_column(
        Enum(ImageProcessingStatus),
        nullable=False,
        default=ImageProcessingStatus.pending,
    )

    gallery: Mapped["Gallery"] = relationship(back_populates="images")
    previews: Mapped[list["ImagePreview"]] = relationship(back_populates="image", cascade="all, delete-orphan")
    selection_events: Mapped[list["SelectionEvent"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )


class ImagePreview(Base):
    __tablename__ = "image_previews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant: Mapped[PreviewVariant] = mapped_column(Enum(PreviewVariant), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    image: Mapped["Image"] = relationship(back_populates="previews")

    __table_args__ = (Index("ix_preview_image_variant", "image_id", "variant", unique=True),)


class SelectionEvent(Base):
    """Append-only event log for model selections. Never UPDATE or DELETE rows."""

    __tablename__ = "selection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True
    )
    share_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("share_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[SelectionAction] = mapped_column(Enum(SelectionAction), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    image: Mapped["Image"] = relationship(back_populates="selection_events")
    share_session: Mapped["ShareSession"] = relationship(back_populates="selection_events")


class ShareSession(Base):
    __tablename__ = "share_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    gallery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    gallery: Mapped["Gallery"] = relationship(back_populates="share_sessions")
    selection_events: Mapped[list["SelectionEvent"]] = relationship(
        back_populates="share_session", cascade="all, delete-orphan"
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gallery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    gallery: Mapped["Gallery | None"] = relationship(back_populates="audit_entries")


class NotificationConfig(TimestampMixin, Base):
    __tablename__ = "notification_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webhook_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    events: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    user: Mapped["User"] = relationship(back_populates="notification_configs")
    deliveries: Mapped[list["NotificationDelivery"]] = relationship(
        back_populates="config", cascade="all, delete-orphan"
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    config: Mapped["NotificationConfig"] = relationship(back_populates="deliveries")


class PendingSignup(Base):
    __tablename__ = "pending_signups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    verification_token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verification_token_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
