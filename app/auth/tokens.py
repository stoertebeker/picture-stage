import hashlib
import secrets
import uuid
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
    expires_at: datetime | None = None
    fingerprint: str | None = None


def create_access_token(user_id: str) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    # iat anchors the token in time so it can be rejected if issued before the
    # user's tokens_valid_after cut-off (see app/auth/dependencies.py).
    payload = {"sub": user_id, "iat": now, "exp": expire, "type": "access", "jti": secrets.token_urlsafe(32)}
    encoded: str = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return encoded


def decode_access_token(token: str) -> TokenData | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM], options={"require_exp": True})
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        iat = payload.get("iat")
        exp = payload.get("exp")
        if not isinstance(user_id, str) or not user_id or type(iat) is not int or type(exp) is not int:
            return None
        issued_at = datetime.fromtimestamp(iat, tz=UTC)
        # python-jose accepts a token throughout its exp second. Retain its
        # revocation until the following second, including for legacy no-jti JWTs.
        expires_at = datetime.fromtimestamp(exp, tz=UTC) + timedelta(seconds=1)
        # Hash only the authenticated signing input AFTER HS256 validation.
        # Signature base64 padding/pad-bit aliases accepted by jose must identify
        # the same credential. Historical identical JWTs are one credential;
        # new random jtis distinguish even simultaneous logins.
        signing_input = token.rsplit(".", 1)[0]
        fingerprint = hashlib.sha256(signing_input.encode("ascii")).hexdigest()
        return TokenData(user_id, issued_at, expires_at, fingerprint)
    except (JWTError, ValueError, TypeError, OverflowError, OSError):
        return None


def generate_verification_token() -> str:
    return f"v1.{uuid.uuid4().hex}.{secrets.token_urlsafe(32)}"


def verification_lookup_id(token: str) -> uuid.UUID | None:
    """Extract the locator of a new token; old opaque tokens have no locator."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1" or len(parts[1]) != 32 or not parts[2]:
        return None
    try:
        locator = uuid.UUID(hex=parts[1])
    except ValueError:
        return None
    return locator if locator.hex == parts[1] else None
