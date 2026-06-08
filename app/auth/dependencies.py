import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import TokenData, decode_access_token
from app.db.models import LOGIN_ALLOWED_STATUSES, User, UserStatus
from app.db.session import get_db

bearer_scheme = HTTPBearer()


def _token_revoked(user: User, token_data: TokenData) -> bool:
    """True if the token was issued before the user's invalidation cut-off.

    Set on admin password-reset / account-lock so already-issued stateless JWTs
    stop working immediately. Both timestamps come from the server clock, so a
    client/server time skew is irrelevant here.
    """
    cutoff = user.tokens_valid_after
    return cutoff is not None and token_data.issued_at < cutoff


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    token_data = decode_access_token(token)
    if token_data is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    try:
        uid = uuid.UUID(token_data.user_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload") from err

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if _token_revoked(user, token_data):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    return user


async def require_active_user(user: User = Depends(get_current_user)) -> User:
    if user.status not in LOGIN_ALLOWED_STATUSES:
        detail = "Account is disabled" if user.status == UserStatus.disabled else "Account not yet approved"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.status != UserStatus.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def get_user_from_cookie(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    token = request.cookies.get("session")
    if not token:
        return None
    token_data = decode_access_token(token)
    if token_data is None:
        return None
    try:
        uid = uuid.UUID(token_data.user_id)
    except ValueError:
        return None
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is not None and _token_revoked(user, token_data):
        return None
    # Expose to templates (admin nav menu + pending badge) without touching every route.
    request.state.current_user = user
    return user


async def require_authenticated_page(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = await get_user_from_cookie(request, db)
    if user is None or user.status not in LOGIN_ALLOWED_STATUSES:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
