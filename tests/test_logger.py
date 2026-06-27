"""Tests for logging configuration."""

import logging
import os
from unittest.mock import patch

import structlog


def test_is_running_in_kubernetes_true():
    """Test K8s detection when env var is set."""
    with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}):
        from stampbot.logger import is_running_in_kubernetes

        assert is_running_in_kubernetes() is True


def test_is_running_in_kubernetes_false():
    """Test K8s detection when env var is not set."""
    env = os.environ.copy()
    env.pop("KUBERNETES_SERVICE_HOST", None)
    with patch.dict(os.environ, env, clear=True):
        from stampbot.logger import is_running_in_kubernetes

        assert is_running_in_kubernetes() is False


def test_get_log_renderer_json():
    """Test JSON renderer selection."""
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "json"
        # Need to reimport to pick up the patched settings
        from stampbot.logger import _get_log_renderer

        renderer = _get_log_renderer()
        assert isinstance(renderer, structlog.processors.JSONRenderer)


def test_get_log_renderer_console():
    """Test console renderer selection."""
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "console"
        from stampbot.logger import _get_log_renderer

        renderer = _get_log_renderer()
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)


def test_get_log_renderer_auto_kubernetes():
    """Test auto renderer in Kubernetes environment."""
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "auto"
        with patch("stampbot.logger.is_running_in_kubernetes", return_value=True):
            from stampbot.logger import _get_log_renderer

            renderer = _get_log_renderer()
            assert isinstance(renderer, structlog.processors.JSONRenderer)


def test_get_log_renderer_auto_local():
    """Test auto renderer in local environment."""
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "auto"
        with patch("stampbot.logger.is_running_in_kubernetes", return_value=False):
            from stampbot.logger import _get_log_renderer

            renderer = _get_log_renderer()
            assert isinstance(renderer, structlog.dev.ConsoleRenderer)


def test_get_log_renderer_unknown_format():
    """Test fallback to JSON for unknown format."""
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "unknown_format"
        from stampbot.logger import _get_log_renderer

        renderer = _get_log_renderer()
        assert isinstance(renderer, structlog.processors.JSONRenderer)


def test_get_logger():
    """Test getting a logger instance."""
    from stampbot.logger import get_logger

    logger = get_logger("test_module")
    assert logger is not None


def test_configure_logging_with_otel_enabled():
    """Test configure_logging instruments logging when OTEL is enabled."""
    with (
        patch("stampbot.logger.settings") as mock_settings,
        patch("stampbot.logger.LoggingInstrumentor") as mock_instrumentor,
    ):
        mock_settings.log_format = "json"
        mock_settings.log_level = "INFO"
        mock_settings.otel_enabled = True

        from stampbot.logger import configure_logging

        configure_logging()

        # Verify LoggingInstrumentor was called
        mock_instrumentor.return_value.instrument.assert_called_once_with(set_logging_format=True)


def test_configure_logging_without_otel():
    """Test configure_logging does not instrument when OTEL is disabled."""
    with (
        patch("stampbot.logger.settings") as mock_settings,
        patch("stampbot.logger.LoggingInstrumentor") as mock_instrumentor,
    ):
        mock_settings.log_format = "json"
        mock_settings.log_level = "INFO"
        mock_settings.otel_enabled = False

        from stampbot.logger import configure_logging

        configure_logging()

        # Verify LoggingInstrumentor was NOT called
        mock_instrumentor.return_value.instrument.assert_not_called()


def test_configure_logging_emits_logger_name(capsys):
    """Test that log records include the logger name under the "logger" key.

    The name passed to get_logger (typically __name__) should be surfaced in
    the rendered output via the add_logger_name processor.
    """
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "json"
        mock_settings.log_level = "INFO"
        mock_settings.otel_enabled = False

        from stampbot.logger import configure_logging, get_logger

        configure_logging()

        logger = get_logger("stampbot.test_module")
        logger.info("hello")

        captured = capsys.readouterr()
        assert '"logger": "stampbot.test_module"' in captured.out


def test_configure_logging_installs_stdlib_handler():
    """Test that configure_logging installs a ProcessorFormatter on the root logger.

    This ensures stdlib loggers (e.g. uvicorn) produce structured JSON output
    rather than raw plaintext.
    """
    with patch("stampbot.logger.settings") as mock_settings:
        mock_settings.log_format = "json"
        mock_settings.log_level = "INFO"
        mock_settings.otel_enabled = False

        from stampbot.logger import configure_logging

        configure_logging()

        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, structlog.stdlib.ProcessorFormatter)
