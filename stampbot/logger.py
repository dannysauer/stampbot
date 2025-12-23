# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Structured logging configuration."""

import logging
import os
import sys

import structlog
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from stampbot.config import settings


def is_running_in_kubernetes() -> bool:
    """Detect if the application is running inside a Kubernetes pod.

    Checks for the KUBERNETES_SERVICE_HOST environment variable, which is
    automatically set by Kubernetes for all pods.

    Returns:
        True if running in Kubernetes, False otherwise.
    """
    return "KUBERNETES_SERVICE_HOST" in os.environ


def _get_log_renderer() -> structlog.types.Processor:
    """Determine the appropriate log renderer based on configuration.

    Returns:
        JSON renderer for Kubernetes/production, console renderer for local dev.
    """
    log_format = settings.log_format.lower()

    if log_format == "json":
        return structlog.processors.JSONRenderer()
    elif log_format == "console":
        return structlog.dev.ConsoleRenderer(colors=True)
    elif log_format == "auto":
        # Auto-detect: use JSON in Kubernetes, console otherwise
        if is_running_in_kubernetes():
            return structlog.processors.JSONRenderer()
        else:
            return structlog.dev.ConsoleRenderer(colors=True)
    else:
        # Unknown format, default to JSON for safety
        return structlog.processors.JSONRenderer()


def configure_logging() -> None:
    """Configure structured logging with structlog."""
    renderer = _get_log_renderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.log_level),
    )

    # Instrument logging with OpenTelemetry if enabled
    if settings.otel_enabled:
        LoggingInstrumentor().instrument(set_logging_format=True)


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        Configured structlog BoundLogger instance.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
