import os

# Settings defaults to ENVIRONMENT=production, which would reject the CHANGE_ME
# default secrets at import time. The test suite runs without a populated .env,
# so force development here before app.config is imported. Real env vars (e.g.
# in CI) still win via setdefault.
os.environ.setdefault("ENVIRONMENT", "development")
