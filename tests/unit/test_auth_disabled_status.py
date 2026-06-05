"""Tests for the 'disabled' user status and login whitelist (S1).

Verifies that:
- LOGIN_ALLOWED_STATUSES only contains active + admin (pending/disabled denied)
- require_active_user lets active/admin through and rejects pending/disabled
- The rejection detail distinguishes 'disabled' from 'pending'

These are pure-logic tests with a lightweight stub user, so they need no database
(the sandbox blocks localhost; DB-backed flows are covered in CI integration tests).
"""

from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from app.auth.dependencies import require_active_user
from app.db.models import LOGIN_ALLOWED_STATUSES, UserStatus


@dataclass
class _StubUser:
    """Minimal stand-in exposing only the attribute the guard reads."""

    status: UserStatus


def test_whitelist_contains_only_active_and_admin() -> None:
    assert LOGIN_ALLOWED_STATUSES == frozenset({UserStatus.active, UserStatus.admin})
    assert UserStatus.pending not in LOGIN_ALLOWED_STATUSES
    assert UserStatus.disabled not in LOGIN_ALLOWED_STATUSES


@pytest.mark.parametrize("status", [UserStatus.active, UserStatus.admin])
async def test_require_active_user_allows_permitted_statuses(status: UserStatus) -> None:
    user = _StubUser(status=status)
    result = await require_active_user(user=user)  # type: ignore[arg-type]
    assert result is user


async def test_require_active_user_rejects_disabled() -> None:
    user = _StubUser(status=UserStatus.disabled)
    with pytest.raises(HTTPException) as exc:
        await require_active_user(user=user)  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail.lower()


async def test_require_active_user_rejects_pending() -> None:
    user = _StubUser(status=UserStatus.pending)
    with pytest.raises(HTTPException) as exc:
        await require_active_user(user=user)  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert "approved" in exc.value.detail.lower()
