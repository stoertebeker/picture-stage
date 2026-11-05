"""Unit tests for the central logging configuration (picture-stage-vblf).

Verifies the app loggers become visible at INFO, the root logger gets a handler,
uvicorn's loggers stay non-propagating (no double logging), and the config keeps
``disable_existing_loggers`` False (so uvicorn's own loggers are not disabled).
"""

import logging
import subprocess
import sys
import textwrap
from pathlib import Path
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


def test_reconfigure_after_alembic_restores_child_logs_without_duplicates() -> None:
    # Isolate the real fileConfig/dictConfig sequence from pytest's handlers and
    # import order. Nested loggers must work even when their parents are only
    # PlaceHolders in logging's registry.
    result = subprocess.run(  # noqa: S603 -- current interpreter and fixed test code
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
                import logging
                import logging.config

                from app.config import settings
                from app.images import storage_cleanup
                from app.logging_config import configure_logging

                settings.log_level = "INFO"
                loggers = [storage_cleanup.logger, logging.getLogger("app.regression.deep.module")]
                configure_logging()
                assert all(not logger.disabled for logger in loggers)
                logging.config.fileConfig("alembic.ini")
                assert all(logger.disabled for logger in loggers)

                for iteration in range(2):
                    configure_logging()
                    for index, logger in enumerate(loggers):
                        assert not logger.disabled, logger.name
                        logger.info("app-info-%s-%s", iteration, index)
                        logger.error("app-error-%s-%s", iteration, index)
                    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                        logger = logging.getLogger(name)
                        assert not logger.disabled, name
                        logger.info("server-%s-%s", iteration, name)

                logging.getLogger("app.imported_later.worker").info("late-app-info")
                """),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    for iteration in range(2):
        for index in range(2):
            for level in ("info", "error"):
                marker = f"app-{level}-{iteration}-{index}"
                assert sum(line.endswith(marker) for line in lines) == 1, result.stdout
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            marker = f"server-{iteration}-{name}"
            assert sum(line.endswith(marker) for line in lines) == 1, result.stdout
    assert sum(line.endswith("late-app-info") for line in lines) == 1, result.stdout
    assert result.stderr == ""
