"""Durable session budgets cannot be configured as unlimited or non-finite."""

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize("field", ["max_share_sessions_per_gallery", "share_session_ttl_seconds"])
@pytest.mark.parametrize("value", [0, -1, "inf", "nan", 1.5, 2**31])
def test_session_limits_require_bounded_positive_integers(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="test", **{field: value})


def test_session_limit_defaults_and_valid_custom_values():
    defaults = Settings(_env_file=None, environment="test")
    assert defaults.max_share_sessions_per_gallery == 1000
    assert defaults.share_session_ttl_seconds == 43200
    custom = Settings(
        _env_file=None, environment="test", max_share_sessions_per_gallery=3, share_session_ttl_seconds=60
    )
    assert (custom.max_share_sessions_per_gallery, custom.share_session_ttl_seconds) == (3, 60)
