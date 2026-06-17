"""Unit tests for the central logging configuration (picture-stage-vblf).

Verifies the app loggers become visible at INFO, the root logger gets a handler,
uvicorn's loggers stay non-propagating (no double logging), and the config keeps
``disable_existing_loggers`` False (so uvicorn's own loggers are not disabled).
"""

import logging
from unittest.mock import patch

from app.config import settings
from app.logging_config import build_logging_config, configure_logging


def test_configure_logging_sets_app_logger_to_info() -> None:
    configure_logging()
    assert logging.getLogger("app").getEffectiveLevel() == logging.INFO


def test_root_logger_has_handler() -> None:
    configure_logging()
    assert logging.getLogger().handlers, "root logger should have a stream handler"


def test_uvicorn_loggers_do_not_propagate() -> None:
    configure_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).propagate is False, f"{name} must not propagate (avoids double logging)"


def test_uvicorn_loggers_keep_a_handler() -> None:
    # Regression: without an explicit handler the uvicorn loggers lose theirs
    # (Alembic's fileConfig strips them) and request/startup logs disappear.
    configure_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).handlers, f"{name} must keep a handler (no lost logs)"


def test_build_config_keeps_existing_loggers_enabled() -> None:
    # Critical: True would disable uvicorn's own loggers.
    assert build_logging_config()["disable_existing_loggers"] is False


def test_build_config_respects_log_level_setting() -> None:
    with patch.object(settings, "log_level", "debug"):  # also checks case-normalisation
        cfg = build_logging_config()
    assert cfg["root"]["level"] == "DEBUG"
    assert cfg["loggers"]["app"]["level"] == "DEBUG"
