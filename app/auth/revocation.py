"""Shared bearer/cookie revocation checks and logout transaction."""

from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import TokenData, decode_access_token
from app.config import settings
from app.db.models import RevokedToken

REVOKED_TOKEN_CLEANUP_BATCH = 100


async def is_token_revoked(db: AsyncSession, token_data: TokenData) -> bool:
    # No process-local negative cache: every request observes committed logout.
    return (
        await db.scalar(select(RevokedToken.fingerprint).where(RevokedToken.fingerprint == token_data.fingerprint))
        is not None
    )


async def cleanup_revoked_tokens(db: AsyncSession, now: datetime) -> None:
    """Bounded, expiry-indexed cleanup, participating in the logout transaction."""
    expired = (
        select(RevokedToken.fingerprint)
        .where(RevokedToken.expires_at <= now)
        .order_by(RevokedToken.expires_at)
        .limit(REVOKED_TOKEN_CLEANUP_BATCH)
        .with_for_update(skip_locked=True)
    )
    await db.execute(delete(RevokedToken).where(RevokedToken.fingerprint.in_(expired)))


async def logout_credential(request: Request, db: AsyncSession) -> RedirectResponse:
    """Revoke the presented credential before returning a cookie deletion."""
    cookie = request.cookies.get("session")
    cookie_data = decode_access_token(cookie) if cookie else None
    authorization = request.headers.get("authorization")
    if authorization is not None:
        parts = authorization.split()
        token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None
        token_data = decode_access_token(token) if token else None
        # Never silently choose a second login, even when the explicit header
        # is malformed. Matching signature aliases refer to one credential.
        if cookie_data is not None and (token_data is None or token_data.fingerprint != cookie_data.fingerprint):
            raise HTTPException(status_code=409, detail="Conflicting logout credentials")
    else:
        token_data = cookie_data

    if token_data is not None:
        await cleanup_revoked_tokens(db, datetime.now(UTC))
        await db.execute(
            insert(RevokedToken)
            .values(fingerprint=token_data.fingerprint, expires_at=token_data.expires_at)
            .on_conflict_do_nothing(index_elements=[RevokedToken.fingerprint])
        )
        # Insert/commit errors propagate: never acknowledge a logout or delete
        # the browser credential before its revocation is durable.
        await db.commit()

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="session", path="/", secure=settings.cookie_secure, samesite="lax", httponly=True)
    return response
