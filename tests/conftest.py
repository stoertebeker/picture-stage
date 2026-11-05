import os

import pytest

# Settings defaults to ENVIRONMENT=production, which would reject the CHANGE_ME
# default secrets at import time. The test suite runs without a populated .env,
# so force development here before app.config is imported. Real env vars (e.g.
# in CI) still win via setdefault.
os.environ.setdefault("ENVIRONMENT", "development")
# Normal tests exercise signup independently; enabled CAPTCHA tests opt in.
os.environ["CAPTCHA_ENABLED"] = "false"
# A test must never reset counters in an operator's configured Redis backend.
os.environ["RATELIMIT_STORAGE_URL"] = "memory://"


@pytest.fixture(autouse=True)
def _isolated_rate_limit_counters():
    """Keep enforcement enabled while preventing cross-test counter leakage."""
    from app.security.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()
