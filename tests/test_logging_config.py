"""Tests for logging configuration."""

from __future__ import annotations

import logging

from autoforge.logging_config import setup_logging


class TestSetupLogging:
    def test_default_level_is_info(self) -> None:
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_explicit_level(self) -> None:
        setup_logging(level_name="debug")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self) -> None:
        setup_logging(level_name="bogus")
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_log_file(self, tmp_path) -> None:
        log_file = tmp_path / "test.log"
        setup_logging(log_file=str(log_file))
        logger = logging.getLogger("test_log_file_output")
        logger.info("hello")
        assert log_file.exists()

    def teardown_method(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)
