import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


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
