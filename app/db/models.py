import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    or_,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base, TimestampMixin, generate_uuid
from app.images.capture_date import MISSING_CAPTURE_DATE, parse_exif_date

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


# Allowed gallery status transitions (state machine). Single source of truth for
# both the JSON API (app/galleries/router.py) and the HTMX frontend
# (app/frontend/galleries.py) — see picture-stage-bkw.
ALLOWED_TRANSITIONS: dict[GalleryStatus, set[GalleryStatus]] = {
    GalleryStatus.draft: {GalleryStatus.shared},
    GalleryStatus.shared: {GalleryStatus.completed},
    GalleryStatus.completed: {GalleryStatus.archived, GalleryStatus.shared},
    GalleryStatus.archived: {GalleryStatus.shared},
}


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


class SetupState(Base):
    """One durable first-run claim, consumed atomically with the first admin."""

    __tablename__ = "setup_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_setup_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RevokedToken(Base):
    """Per-credential logout ledger; independent of user deletion and cutoffs."""

    __tablename__ = "revoked_tokens"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    # JWT exp + one second, matching python-jose's inclusive exp-second check.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


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
    # Per-user override for the global MAX_GALLERIES_PER_USER setting.
    # None = use global default; 0 = unlimited; positive = specific cap.
    gallery_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    galleries: Mapped[list["Gallery"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    notification_configs: Mapped[list["NotificationConfig"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Gallery(TimestampMixin, Base):
    __tablename__ = "galleries"
    __table_args__ = (
        Index("ix_galleries_token_candidates", "share_token_lookup", "id"),
        Index(
            "ix_galleries_legacy_token_ids",
            "id",
            postgresql_where=text("share_token_lookup IS NULL AND share_token_hash IS NOT NULL"),
        ),
    )

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
    share_token_lookup: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorization_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    watermark_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    owner: Mapped["User"] = relationship(back_populates="galleries")
    images: Mapped[list["Image"]] = relationship(back_populates="gallery", cascade="all, delete-orphan")
    share_sessions: Mapped[list["ShareSession"]] = relationship(back_populates="gallery", cascade="all, delete-orphan")
    audit_entries: Mapped[list["AuditLog"]] = relationship(back_populates="gallery", cascade="save-update, merge")


class UploadCleanupIntent(Base):
    """Durable storage ownership for a batch before any Image rows can commit.

    Kept after failed compensation so gallery/user deletion can retry the keys.
    The restrictive FK prevents losing recovery data through an unchecked delete.
    """

    __tablename__ = "upload_cleanup_intents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    gallery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id"), nullable=False, index=True
    )
    image_keys: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StorageUsage(Base):
    """Committed reservations, including objects with uncertain write outcomes."""

    __tablename__ = "storage_usage"
    __table_args__ = (
        CheckConstraint("bytes >= 0 AND objects >= 0 AND unknown_sizes >= 0", name="ck_storage_usage_nonnegative"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    objects: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    unknown_sizes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class StorageAllocation(Base):
    """Quota charge and cleanup ownership persisted before backend I/O.

    Gallery/image IDs deliberately have no FK: copying creates them in another
    transaction, and failed transactions must not erase storage ownership.
    """

    __tablename__ = "storage_allocations"
    __table_args__ = (CheckConstraint("size IS NULL OR size >= 0", name="ck_storage_allocation_size"),)

    key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    gallery_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    image_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


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
    capture_sort_key: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=lambda: MISSING_CAPTURE_DATE)
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

    @validates("exif")
    def normalize_capture_date(self, key: str, value: dict[str, Any] | None) -> dict[str, Any] | None:
        # Shared by API/HTML uploads, duplication and worker metadata updates.
        self.capture_sort_key = parse_exif_date(value) or MISSING_CAPTURE_DATE
        return value

    __table_args__ = (
        Index("ix_images_gallery_order", "gallery_id", "sort_order", "id"),
        Index("ix_images_gallery_filename", "gallery_id", "filename", "id"),
        Index("ix_images_gallery_capture", "gallery_id", "capture_sort_key", "sort_order", "id"),
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


class CurrentSelection(Base):
    """One current state per image; independently version each event dimension."""

    __tablename__ = "current_selections"

    image_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
    )
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    favorited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    favorited_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ShareSession(Base):
    __tablename__ = "share_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    gallery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    authorization_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL preserves the original authorization/retention of legacy visits.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_share_sessions_gallery_expiry",
            "gallery_id",
            "expires_at",
            "id",
            postgresql_where=text("completed_at IS NULL AND expires_at IS NOT NULL"),
        ),
    )

    gallery: Mapped["Gallery"] = relationship(back_populates="share_sessions")
    selection_events: Mapped[list["SelectionEvent"]] = relationship(
        back_populates="share_session", cascade="all, delete-orphan"
    )


class ShareSessionCleanupState(Base):
    """One durable rotating retention checkpoint per gallery, never a grant."""

    __tablename__ = "share_session_cleanup_state"

    gallery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id", ondelete="CASCADE"), primary_key=True
    )
    last_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gallery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("galleries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
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
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verification_token_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verification_lookup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, unique=True, index=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)


class CaptchaRedemption(Base):
    """One durable use per signed signup challenge, shared by all workers."""

    __tablename__ = "captcha_redemptions"

    signature: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


# Keep the append-only event and its projection atomic for every ORM producer.
from sqlalchemy import event as _sa_event  # noqa: E402


@_sa_event.listens_for(SelectionEvent, "after_insert")
def _project_selection_event(mapper: Any, connection: Any, event: SelectionEvent) -> None:
    from sqlalchemy.dialects.postgresql import insert

    dimension = {
        SelectionAction.select: "selected",
        SelectionAction.deselect: "selected",
        SelectionAction.favorite: "favorited",
        SelectionAction.unfavorite: "favorited",
        SelectionAction.comment: "comment",
    }[event.action]
    if dimension == "comment" and event.comment is None:
        return
    value = (
        event.comment if dimension == "comment" else event.action in (SelectionAction.select, SelectionAction.favorite)
    )
    values = {
        "image_id": event.image_id,
        dimension: value,
        dimension + "_at": event.created_at,
        dimension + "_event_id": event.id,
    }
    stmt = insert(CurrentSelection).values(**values)
    table = CurrentSelection.__table__
    newer = or_(
        table.c[dimension + "_at"].is_(None),
        tuple_(table.c[dimension + "_at"], table.c[dimension + "_event_id"])
        < tuple_(stmt.excluded[dimension + "_at"], stmt.excluded[dimension + "_event_id"]),
    )
    connection.execute(
        stmt.on_conflict_do_update(
            index_elements=[table.c.image_id],
            set_={name: stmt.excluded[name] for name in values if name != "image_id"},
            where=newer,
        )
    )
