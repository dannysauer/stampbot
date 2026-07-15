# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""OpenTelemetry configuration and instrumentation."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from stampbot.config import settings
from stampbot.logger import get_logger
from stampbot.version import APP_VERSION

logger = get_logger(__name__)


def configure_telemetry() -> TracerProvider | None:
    """Configure OpenTelemetry if enabled.

    Returns:
        TracerProvider if telemetry is enabled, None otherwise
    """
    if not settings.otel_enabled:
        logger.info("OpenTelemetry disabled")
        return None

    if not settings.otel_endpoint:
        logger.warning("OpenTelemetry enabled but no endpoint configured")
        return None

    try:
        # Create resource
        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": APP_VERSION,
            }
        )

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # TLS is the default. Plaintext export requires an explicit opt-in.
        otel_insecure_requested = settings.get("otel_insecure", False) is True
        endpoint_uses_https = urlparse(settings.otel_endpoint).scheme == "https"
        if otel_insecure_requested and endpoint_uses_https:
            logger.warning("Ignoring plaintext OTLP setting for an HTTPS endpoint")
        otel_insecure = otel_insecure_requested and not endpoint_uses_https
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel_endpoint,
            insecure=otel_insecure,
        )

        # Add span processor
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Set as global tracer provider
        trace.set_tracer_provider(provider)

        logger.info(
            "OpenTelemetry configured with endpoint: %s (%s)",
            settings.otel_endpoint,
            "plaintext" if otel_insecure else "TLS",
            extra={
                "otel_endpoint": settings.otel_endpoint,
                "otel_transport": "plaintext" if otel_insecure else "TLS",
            },
        )

        return provider

    except Exception as e:
        logger.error("Failed to configure OpenTelemetry: %s", e, extra={"error": str(e)})
        return None


def instrument_fastapi(app: Any) -> None:
    """Instrument FastAPI app with OpenTelemetry.

    Args:
        app: FastAPI application instance
    """
    if settings.otel_enabled:
        try:
            FastAPIInstrumentor.instrument_app(app)
            logger.info("FastAPI instrumented with OpenTelemetry")
        except Exception as e:
            logger.error("Failed to instrument FastAPI: %s", e, extra={"error": str(e)})


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer instance for creating spans.

    Args:
        name: Name for the tracer (typically __name__)

    Returns:
        Tracer instance (no-op if telemetry disabled)
    """
    return trace.get_tracer(name)


@contextmanager
def create_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Iterator[Span | None]:
    """Create a span context manager for tracing operations.

    This is a convenience wrapper that handles span creation, attribute setting,
    and exception recording. If OpenTelemetry is disabled, yields None and
    the code block executes without tracing.

    Args:
        name: Name of the span
        attributes: Optional attributes to set on the span
        record_exception: Whether to record exceptions on the span

    Yields:
        The span if telemetry is enabled, None otherwise

    Example:
        with create_span("github.approve_pr", {"repo": "owner/repo", "pr": 123}) as span:
            # do work
            if span:
                span.set_attribute("result", "success")
    """
    if not settings.otel_enabled:
        yield None
        return

    tracer = get_tracer("stampbot")

    with tracer.start_as_current_span(name) as span:
        try:
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
            yield span
        except Exception as e:
            if record_exception:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
            raise


def set_span_error(span: Span | None, error: Exception) -> None:
    """Set error status on a span.

    Args:
        span: The span to update (can be None if telemetry disabled)
        error: The exception that occurred
    """
    if span:
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, str(error)))


def set_span_ok(span: Span | None) -> None:
    """Set OK status on a span.

    Args:
        span: The span to update (can be None if telemetry disabled)
    """
    if span:
        span.set_status(Status(StatusCode.OK))


def add_span_attributes(span: Span | None, attributes: dict[str, Any]) -> None:
    """Add attributes to a span.

    Args:
        span: The span to update (can be None if telemetry disabled)
        attributes: Attributes to add
    """
    if span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
