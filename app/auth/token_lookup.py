"""Indexed token locators and a bounded compatibility path for old tokens."""

import hashlib
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import verify_token
from app.auth.tokens import verification_lookup_id
from app.db.models import PendingSignup

_lock = threading.Lock()
LegacyTokenKind = Literal["gallery", "verification"]
_legacy_running: dict[LegacyTokenKind, int] = {"gallery": 0, "verification": 0}
_MAX_LEGACY_SCANS = 2


def token_locator(token: str) -> bytes:
    """Derive a fixed-size locator for an independently random bearer token."""
    return hashlib.sha256(token.encode()).digest()


@asynccontextmanager
async def legacy_token_scan(kind: LegacyTokenKind) -> AsyncIterator[None]:
    """Limit concurrent legacy scans without retaining an unbounded wait queue.

    This bounds simultaneous work and buffered rows per process; the number of
    verifier operations in an individual legacy scan is still linear.
    """
    with _lock:
        if _legacy_running[kind] >= _MAX_LEGACY_SCANS:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Token lookup is busy; please retry",
                headers={"Retry-After": "1"},
            )
        _legacy_running[kind] += 1
    try:
        yield
    finally:
        with _lock:
            _legacy_running[kind] -= 1


async def resolve_pending_signup(token: str, db: AsyncSession) -> PendingSignup | None:
    """Use a new-token locator, then retain compatibility with old links."""
    locator = verification_lookup_id(token)
    if locator is not None:
        candidate = await db.scalar(select(PendingSignup).where(PendingSignup.verification_lookup_id == locator))
        if candidate is not None and verify_token(
            token, candidate.verification_token_hash, candidate.verification_token_salt
        ):
            return candidate

    # A legacy signup has no recoverable locator. Stream rather than load the
    # complete pending-signup table; old instances can still create such rows.
    async with legacy_token_scan("verification"):
        result = await db.stream(
            select(PendingSignup)
            .where(PendingSignup.verification_lookup_id.is_(None), PendingSignup.verification_token_hash != b"")
            .execution_options(yield_per=100)
        )
        try:
            async for signup in result.scalars():
                if verify_token(token, signup.verification_token_hash, signup.verification_token_salt):
                    return signup
        finally:
            await result.close()
    return None
