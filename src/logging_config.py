"""Shared logging configuration for agent and runner."""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def setup_logging(
    level_name: str | None = None,
    log_file: str | None = None,
) -> None:
    """Configure the root logger with console and optional file output.

    Priority for log level: level_name arg > LOG_LEVEL env var > INFO default.

    Args:
        level_name: Log level string (debug, info, warning, error, critical).
        log_file: Optional path to a log file. Logs are written to both
            stdout and the file when provided.
    """
    env_level = os.environ.get("LOG_LEVEL", "").upper()
    resolved = (level_name or "").upper() or env_level or "INFO"

    if resolved not in VALID_LEVELS:
        resolved = "INFO"

    level = getattr(logging, resolved)

    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
