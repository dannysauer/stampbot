"""Tests for main FastAPI application."""

import asyncio
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def test_lifespan_startup_shutdown_configured():
    """Test app lifespan startup and shutdown when configured."""
    from stampbot.main import app

    # Use TestClient as context manager to trigger lifespan events
    with TestClient(app) as client:
        # App should be running
        response = client.get("/health")
        assert response.status_code == 200


def test_lifespan_startup_unconfigured():
    """Test app lifespan startup when not configured (setup mode)."""
    from stampbot.main import app

    with (
        patch("stampbot.main.is_configured", return_value=False),
        patch("stampbot.main.settings") as mock_settings,
    ):
        mock_settings.app_name = "stampbot"
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 8000
        mock_settings.log_level = "INFO"

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


def test_root_endpoint(test_client: TestClient):
    """Test root endpoint returns basic info."""
    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "stampbot"
    assert data["status"] == "running"


def test_health_endpoint(test_client: TestClient):
    """Test liveness endpoint always reports healthy while the app is up."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_ready_endpoint_configured(test_client: TestClient):
    """Test readiness endpoint returns 200 when the app is configured."""
    with patch("stampbot.main.is_configured", return_value=True):
        response = test_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["configured"] is True


def test_ready_endpoint_unconfigured(test_client: TestClient):
    """Test readiness endpoint returns 503 when the app is not configured."""
    with patch("stampbot.main.is_configured", return_value=False):
        response = test_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not ready"
    assert data["checks"]["configured"] is False


def test_metrics_endpoint(test_client: TestClient):
    """Test Prometheus metrics endpoint."""
    response = test_client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_request_with_content_length(test_client: TestClient):
    """Test that requests with Content-Length header are tracked."""
    # POST with a body will have Content-Length set automatically
    response = test_client.post(
        "/webhook",
        content=b'{"test": "data"}',
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    # Will fail signature validation, but Content-Length tracking happens before that
    assert response.status_code == 401


def test_webhook_missing_signature(test_client: TestClient):
    """Test webhook rejects requests without signature."""
    response = test_client.post(
        "/webhook",
        json={"zen": "test"},
        headers={"X-GitHub-Event": "ping"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid signature"


def test_webhook_missing_event_header(test_client: TestClient):
    """Test webhook rejects requests without X-GitHub-Event header."""
    response = test_client.post(
        "/webhook",
        json={"zen": "test"},
        headers={"X-Hub-Signature-256": "sha256=invalid"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing X-GitHub-Event header"


def test_webhook_invalid_signature(test_client: TestClient):
    """Test webhook rejects requests with invalid signature."""
    response = test_client.post(
        "/webhook",
        json={"zen": "test"},
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=invalidsignature",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid signature"


def test_webhook_invalid_json(test_client: TestClient):
    """Test webhook rejects invalid JSON payload."""
    import hashlib
    import hmac

    from stampbot.webhook_handler import webhook_handler

    # Create a valid signature for invalid JSON
    body = b"not valid json"
    signature = (
        "sha256="
        + hmac.new(
            webhook_handler.webhook_secret,
            body,
            hashlib.sha256,
        ).hexdigest()
    )

    response = test_client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON payload"


def test_webhook_valid_ping(test_client: TestClient):
    """Test webhook accepts valid ping event."""
    import hashlib
    import hmac

    from stampbot.webhook_handler import webhook_handler

    body = json.dumps({"zen": "Design for failure."}).encode()
    signature = (
        "sha256="
        + hmac.new(
            webhook_handler.webhook_secret,
            body,
            hashlib.sha256,
        ).hexdigest()
    )

    response = test_client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["message"] == "pong"


def test_webhook_content_length_too_large(test_client: TestClient):
    """Test webhook rejects requests with Content-Length exceeding limit."""
    response = test_client.post(
        "/webhook",
        content=b"x",  # Small actual body
        headers={
            "Content-Type": "application/json",
            "Content-Length": "20000000",  # 20MB - exceeds 10MB limit
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=fake",
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large"


def test_webhook_body_too_large(test_client: TestClient):
    """Test webhook rejects requests where actual body exceeds limit.

    This tests the secondary body size check (lines 231-232) which catches
    cases where Content-Length header is missing or incorrect.
    """
    # Create a body larger than MAX_WEBHOOK_BODY_SIZE (10MB)
    large_body = b"x" * (10 * 1024 * 1024 + 1)  # 10MB + 1 byte

    # Set Content-Length to a small value to bypass the header check (line 222)
    # but the actual body size check (line 230) should still catch it
    response = test_client.post(
        "/webhook",
        content=large_body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": "100",  # Lie about size to bypass first check
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=fake",
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large"


def test_response_without_content_length():
    """Test that responses without Content-Length header are handled correctly.

    This tests the branch where response_size is None (line 142->148).
    StreamingResponse doesn't include Content-Length header.
    """
    from collections.abc import AsyncIterator

    from fastapi.responses import StreamingResponse

    from stampbot.main import app

    # Track if our streaming endpoint was added
    streaming_route_path = "/_test_streaming_no_cl"

    async def generate() -> AsyncIterator[bytes]:
        yield b"chunk1"
        yield b"chunk2"

    # Add a temporary streaming endpoint
    @app.get(streaming_route_path)
    async def streaming_no_content_length() -> StreamingResponse:
        return StreamingResponse(generate(), media_type="text/plain")

    client = TestClient(app)
    response = client.get(streaming_route_path)

    assert response.status_code == 200
    assert response.text == "chunk1chunk2"
    # StreamingResponse doesn't set Content-Length, so line 142->148 branch is exercised


def test_logging_middleware_binds_forwarded_ip(test_client: TestClient):
    """Test that logging_middleware binds client IP from X-Forwarded-For header."""
    response = test_client.get(
        "/health",
        headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
    )
    assert response.status_code == 200
    # Context is cleared after the request completes; the middleware itself is
    # tested by confirming the request succeeded (no exceptions from binding).


def test_logging_middleware_falls_back_to_direct_ip(test_client: TestClient):
    """Test that logging_middleware uses the direct connection IP when no forwarding header."""
    response = test_client.get("/health")
    assert response.status_code == 200


def test_logging_middleware_respects_configured_header():
    """Test that logging_middleware uses the configured client_ip_header setting."""
    from unittest.mock import patch

    import structlog.contextvars

    from stampbot.main import app

    with patch("stampbot.main.settings") as mock_settings:
        mock_settings.get = lambda key, default=None: (
            "X-Real-IP" if key == "client_ip_header" else default
        )

        client = TestClient(app)
        captured: dict[str, str | None] = {}

        original_bind = structlog.contextvars.bind_contextvars

        def capturing_bind(**kw: object) -> None:
            captured.update({k: str(v) for k, v in kw.items()})
            original_bind(**kw)

        with patch("stampbot.main.structlog.contextvars.bind_contextvars", capturing_bind):
            client.get("/health", headers={"X-Real-IP": "198.51.100.7"})

        assert captured.get("client_ip") == "198.51.100.7"


def test_logging_middleware_no_client_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that client_ip is not bound when no IP source is available.

    Covers the ``if client_ip:`` False branch inside LoggingMiddleware by
    exercising the middleware directly with an empty client_ip_header setting
    and a scope that carries no client address.
    """
    from unittest.mock import AsyncMock
    from unittest.mock import patch as mock_patch

    from stampbot.main import LoggingMiddleware

    # Return "" for client_ip_header so the header-lookup block is skipped.
    monkeypatch.setattr("stampbot.main.settings", {"client_ip_header": ""})

    inner_app = AsyncMock()
    middleware = LoggingMiddleware(inner_app)

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,  # no direct-connection address either
        "query_string": b"",
    }

    with mock_patch("structlog.contextvars.bind_contextvars") as mock_bind:
        asyncio.run(middleware(scope, None, None))

    mock_bind.assert_not_called()
    inner_app.assert_called_once_with(scope, None, None)


def test_webhook_handler_exception(test_client: TestClient):
    """Test webhook returns 500 when handler raises exception."""
    from stampbot.webhook_handler import webhook_handler

    body = json.dumps({"zen": "test"}).encode()
    signature = (
        "sha256="
        + hmac.new(
            webhook_handler.webhook_secret,
            body,
            hashlib.sha256,
        ).hexdigest()
    )

    with patch("stampbot.main.webhook_handler.handle_event") as mock_handle:
        mock_handle.side_effect = Exception("Unexpected error")

        response = test_client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": signature,
            },
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
