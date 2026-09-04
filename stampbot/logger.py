# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Structured logging configuration."""

import logging
import os
import sys

import structlog

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
    """Configure structured logging with structlog.

    Routes both structlog-native records and foreign stdlib records (e.g.
    from uvicorn) through the same processor chain so all log output uses
    a consistent format (JSON in production, coloured console locally).
    """
    log_level_int = logging.getLevelName(settings.log_level)
    renderer = _get_log_renderer()

    # Processors shared by structlog-native and foreign (stdlib) log records.
    # Applied to every record before the final renderer.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        # Emit the logger name (passed to get_logger, typically __name__) under
        # the "logger" key, for both structlog-native and foreign stdlib records.
        structlog.stdlib.add_logger_name,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # Wrap the event dict so stdlib's ProcessorFormatter can render it.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        # Route structlog through stdlib so all output shares one handler.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ProcessorFormatter ensures uvicorn access logs and every other stdlib
    # logger produce the same structured output as application logs.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ExtraAdder(),
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level_int)

    # Trace-context log fields are added by stampbot.telemetry.configure_telemetry
    # once the tracer provider exists, so records carry the service name.


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        Configured structlog BoundLogger instance.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
