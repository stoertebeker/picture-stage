import secrets
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


class TokenData(NamedTuple):
    """Decoded access-token claims relevant to authentication."""

    user_id: str
    issued_at: datetime


def create_access_token(user_id: str) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    # iat anchors the token in time so it can be rejected if issued before the
    # user's tokens_valid_after cut-off (see app/auth/dependencies.py).
    payload = {"sub": user_id, "iat": now, "exp": expire, "type": "access"}
    encoded: str = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return encoded


def decode_access_token(token: str) -> TokenData | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        user_id: str | None = payload.get("sub")
        iat: int | None = payload.get("iat")
        if user_id is None or iat is None:
            return None
        return TokenData(user_id=user_id, issued_at=datetime.fromtimestamp(iat, tz=UTC))
    except JWTError:
        return None


def generate_verification_token() -> str:
    return secrets.token_urlsafe(32)
