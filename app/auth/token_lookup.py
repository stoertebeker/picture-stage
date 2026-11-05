"""Indexed token locators and a bounded compatibility path for old tokens."""

import hashlib
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.lookup_progress import LOOKUP_WINDOW, RETRY_HEADER, RETRY_VALUE, LookupProgress
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

    Callers also bound each admitted request to a fixed candidate window;
    historical links can advance through several requests without a full scan.
    """
    with _lock:
        if _legacy_running[kind] >= _MAX_LEGACY_SCANS:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Token lookup is busy; please retry",
                headers={"Retry-After": "1", RETRY_HEADER: RETRY_VALUE, "Cache-Control": "private, no-store"},
            )
        _legacy_running[kind] += 1
    try:
        yield
    finally:
        with _lock:
            _legacy_running[kind] -= 1


async def resolve_pending_signup(token: str, db: AsyncSession) -> PendingSignup | None:
    """Resolve one indexed candidate or a bounded legacy ID window.

    Old links have no recoverable locator and no expiry. Signed progress lets
    them traverse any backlog without making one public request scan it all.
    Eligibility is checked only after selecting IDs, including cleared rows.
    """
    progress = LookupProgress("verification", token)
    cursor = progress.read()
    # PendingSignup.id is PostgreSQL int4, including imported negative IDs.
    # Reject authenticated but out-of-range values before constructing SQL.
    if cursor is not None and (cursor[0] != "l" or int(cursor[1], 16) > 2**32 - 1):
        cursor = None
    remaining = LOOKUP_WINDOW
    locator = verification_lookup_id(token) if cursor is None else None
    if locator is not None:
        candidate = await db.scalar(select(PendingSignup).where(PendingSignup.verification_lookup_id == locator))
        if candidate is not None and candidate.verification_token_hash and candidate.verification_token_salt:
            remaining -= 1
            if verify_token(token, candidate.verification_token_hash, candidate.verification_token_salt):
                progress.clear()
                return candidate

    async with legacy_token_scan("verification"):
        window = select(PendingSignup.id).where(PendingSignup.verification_lookup_id.is_(None))
        if cursor is not None:
            window = window.where(PendingSignup.id > int(cursor[1], 16) - 2**31)
        ids = list((await db.scalars(window.order_by(PendingSignup.id).limit(remaining + 1))).all())
        if ids:
            candidates = (
                await db.scalars(
                    select(PendingSignup)
                    .where(PendingSignup.id.in_(ids[:remaining]))
                    .execution_options(populate_existing=True)
                )
            ).all()
            for signup in candidates:
                if (
                    signup.verification_token_hash
                    and signup.verification_token_salt
                    and verify_token(token, signup.verification_token_hash, signup.verification_token_salt)
                ):
                    progress.clear()
                    return signup
        if len(ids) > remaining:
            progress.retry("l", f"{ids[remaining - 1] + 2**31:032x}")
    progress.clear()
    return None


async def lock_pending_signup(signup_id: int, db: AsyncSession) -> PendingSignup | None:
    """Serialize lifecycle changes and reload any previously resolved candidate."""
    signup: PendingSignup | None = await db.scalar(
        select(PendingSignup)
        .where(PendingSignup.id == signup_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return signup


async def consume_pending_signup(token: str, db: AsyncSession) -> PendingSignup | None:
    candidate = await resolve_pending_signup(token, db)
    if candidate is None:
        return None
    signup = await lock_pending_signup(candidate.id, db)
    if (
        signup is None
        or signup.email_verified_at is not None
        or not signup.verification_token_hash
        or not signup.verification_token_salt
        or not verify_token(token, signup.verification_token_hash, signup.verification_token_salt)
    ):
        return None
    signup.email_verified_at = datetime.now(UTC)
    signup.verification_token_hash = b""
    signup.verification_token_salt = b""
    signup.verification_lookup_id = None
    await db.commit()
    return signup
