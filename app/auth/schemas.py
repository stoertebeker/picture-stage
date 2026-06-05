import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.db.models import UserStatus


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class SignupResponse(BaseModel):
    message: str
    verification_token: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    locale: str
    email_verified_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PendingSignupResponse(BaseModel):
    id: int
    email: str
    requested_at: datetime

    model_config = {"from_attributes": True}


class LocaleUpdate(BaseModel):
    locale: str


class AdminUserResponse(BaseModel):
    """A user account as seen by an admin in the user-management view."""

    id: uuid.UUID
    email: str
    status: str
    locale: str
    email_verified_at: datetime | None
    created_at: datetime
    galleries_count: int

    model_config = {"from_attributes": True}


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int
    page: int
    per_page: int


class PendingSignupCountResponse(BaseModel):
    count: int


class AdminUserStatusUpdate(BaseModel):
    """Target status for an admin status change (pending is rejected in the router)."""

    status: UserStatus


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8)
