"""Central logging configuration (picture-stage-vblf).

Without this, the app loggers (``logging.getLogger("app.*")``) have no handler and
fall back to Python's ``lastResort`` handler (WARNING threshold), so ``INFO``
diagnostics — preview-worker progress, ProcessPool startup, notification/audit
service logs — are invisible in the container log, while only uvicorn's own
loggers are configured.

``configure_logging()`` installs a ``dictConfig`` that:
- routes the root + ``app`` logger to a stdout ``StreamHandler`` at ``LOG_LEVEL``;
- keeps uvicorn's loggers intact (``disable_existing_loggers=False``) and pins
  them to ``propagate=False`` so their lines are not logged twice via the root
  handler.

Called at import time from ``app.main`` (before the app is created) and as the
``initializer`` of the preview ProcessPool, since spawned worker processes do not
inherit the parent's logging config.
"""

import logging.config
from typing import Any

from app.config import settings

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_logging_config() -> dict[str, Any]:
    level = settings.log_level.upper()
    return {
        "version": 1,
        # Must stay False, otherwise uvicorn's own loggers get disabled.
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": _LOG_FORMAT, "datefmt": _DATE_FORMAT},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            # App loggers inherit the root handler; set their level explicitly.
            "app": {"level": level, "handlers": [], "propagate": True},
            # Give uvicorn's loggers our handler and pin propagate=False so their
            # lines appear (in our format) but are not emitted twice via root.
            # Explicit handlers also re-attach them after Alembic's fileConfig
            # (disable_existing_loggers=True) strips them at startup.
            "uvicorn": {"level": "INFO", "handlers": ["default"], "propagate": False},
            "uvicorn.error": {"level": "INFO", "handlers": ["default"], "propagate": False},
            "uvicorn.access": {"level": "INFO", "handlers": ["default"], "propagate": False},
        },
        "root": {"level": level, "handlers": ["default"]},
    }


def configure_logging() -> None:
    """Apply the central logging config. Idempotent (dictConfig replaces, not appends)."""
    logging.config.dictConfig(build_logging_config())
