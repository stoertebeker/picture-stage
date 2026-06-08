"""Unit tests for stateless JWT invalidation (picture-stage-7kr).

A token carries an ``iat`` (issued-at). A user gets a ``tokens_valid_after``
cut-off on admin password-reset / account-lock; tokens issued before it are
rejected. All timestamps are server-side, so client clock skew is irrelevant.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.auth.dependencies import _token_revoked
from app.auth.tokens import TokenData, create_access_token, decode_access_token

USER_ID = "11111111-1111-1111-1111-111111111111"


def test_token_roundtrip_carries_user_id_and_iat() -> None:
    token = create_access_token(USER_ID)
    data = decode_access_token(token)

    assert data is not None
    assert data.user_id == USER_ID
    assert isinstance(data.issued_at, datetime)
    # iat must be timezone-aware UTC for the cut-off comparison to be sound.
    assert data.issued_at.tzinfo is not None


def test_decode_rejects_garbage() -> None:
    assert decode_access_token("not-a-jwt") is None


def _user(cutoff: datetime | None) -> SimpleNamespace:
    """Minimal user stub — _token_revoked only reads tokens_valid_after."""
    return SimpleNamespace(tokens_valid_after=cutoff)


def test_not_revoked_when_no_cutoff() -> None:
    """NULL cut-off = no invalidation point: existing tokens stay valid."""
    data = TokenData(user_id=USER_ID, issued_at=datetime.now(UTC))
    assert _token_revoked(_user(None), data) is False


def test_revoked_when_issued_before_cutoff() -> None:
    """A token from before the reset/lock must be rejected."""
    now = datetime.now(UTC)
    data = TokenData(user_id=USER_ID, issued_at=now - timedelta(hours=1))
    assert _token_revoked(_user(now), data) is True


def test_not_revoked_when_issued_after_cutoff() -> None:
    """A token issued after the cut-off (legitimate re-login) stays valid."""
    now = datetime.now(UTC)
    data = TokenData(user_id=USER_ID, issued_at=now + timedelta(seconds=1))
    assert _token_revoked(_user(now), data) is False


def test_not_revoked_at_exact_cutoff() -> None:
    """Strict less-than: a token issued at the cut-off instant survives.

    The comparison uses ``<``, so a token minted in the same instant as the
    reset (the legitimate new session) is not killed by it.
    """
    now = datetime.now(UTC)
    data = TokenData(user_id=USER_ID, issued_at=now)
    assert _token_revoked(_user(now), data) is False
