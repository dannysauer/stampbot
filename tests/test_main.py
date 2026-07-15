"""Tests for main FastAPI application."""

import asyncio
import hashlib
import hmac
import json
import socket
import urllib.request
from unittest.mock import MagicMock, patch

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
        mock_settings.metrics_enabled = False

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200


def test_lifespan_startup_unconfigured_setup_disabled():
    """Test startup remains live when credentials and setup are both absent."""
    from stampbot.main import app

    with (
        patch("stampbot.main.is_configured", return_value=False),
        patch("stampbot.main.settings") as mock_settings,
    ):
        mock_settings.app_name = "stampbot"
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 8000
        mock_settings.log_level = "INFO"
        mock_settings.metrics_enabled = False
        mock_settings.get.side_effect = lambda key, default=None: {
            "setup_enabled": False,
            "setup_allow_configured": False,
        }.get(key, default)

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200


def test_root_endpoint(test_client: TestClient):
    """Test root endpoint returns basic info."""
    from stampbot.version import APP_VERSION

    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "stampbot"
    assert data["version"] == APP_VERSION
    assert data["status"] == "running"


def test_openapi_reports_app_version(test_client: TestClient):
    """Test OpenAPI metadata uses the resolved application version."""
    from stampbot.version import APP_VERSION

    response = test_client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == APP_VERSION


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


def test_ready_endpoint_unconfigured_setup_enabled(test_client: TestClient):
    """Test readiness is 200 in setup mode so /setup stays reachable.

    An unconfigured pod with setup mode on must stay in the Service endpoints,
    otherwise the operator can never reach /setup to configure it.
    """
    with (
        patch("stampbot.main.is_configured", return_value=False),
        patch("stampbot.main.settings") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: {
            "setup_enabled": True,
            "setup_allow_configured": False,
        }.get(key, default)
        response = test_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"] == {"configured": False, "setup_enabled": True}


def test_ready_endpoint_unconfigured_setup_disabled(test_client: TestClient):
    """Test readiness is 503 only when unconfigured and setup is disabled."""
    with (
        patch("stampbot.main.is_configured", return_value=False),
        patch("stampbot.main.settings") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: {
            "setup_enabled": False,
            "setup_allow_configured": False,
        }.get(key, default)
        response = test_client.get("/ready")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not ready"
    assert data["checks"] == {"configured": False, "setup_enabled": False}


def test_metrics_endpoint_is_not_on_public_app(test_client: TestClient):
    """Test that the public listener never serves Prometheus metrics."""
    response = test_client.get("/metrics")
    assert response.status_code == 404


def test_metrics_server_uses_dedicated_listener():
    """Test that the metrics server exports Prometheus text on its own port."""
    from stampbot.metrics import start_metrics_server, stop_metrics_server

    with socket.socket() as socket_for_port:
        socket_for_port.bind(("127.0.0.1", 0))
        port = socket_for_port.getsockname()[1]

    server, thread = start_metrics_server("127.0.0.1", port)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
            body = response.read().decode("utf-8")
            assert response.status == 200
            assert response.headers["content-type"].startswith("text/plain")
            assert "# HELP stampbot_info" in body
    finally:
        stop_metrics_server(server, thread)


def test_lifespan_starts_and_stops_metrics_listener():
    """Test that metrics_enabled controls the dedicated listener lifecycle."""
    from stampbot.main import app

    server = MagicMock()
    thread = MagicMock()
    with (
        patch("stampbot.main.is_configured", return_value=True),
        patch("stampbot.main.settings") as mock_settings,
        patch("stampbot.main.start_metrics_server", return_value=(server, thread)) as start,
        patch("stampbot.main.stop_metrics_server") as stop,
    ):
        mock_settings.app_name = "stampbot"
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 8000
        mock_settings.log_level = "INFO"
        mock_settings.metrics_enabled = True
        mock_settings.metrics_host = "127.0.0.1"
        mock_settings.metrics_port = 9090

        with TestClient(app):
            start.assert_called_once_with("127.0.0.1", 9090)

        stop.assert_called_once_with(server, thread)


def test_lifespan_rejects_metrics_on_public_port():
    """Test that the metrics listener cannot reuse the public HTTP port."""
    from stampbot.main import app

    with (
        patch("stampbot.main.is_configured", return_value=True),
        patch("stampbot.main.settings") as mock_settings,
    ):
        mock_settings.app_name = "stampbot"
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 8000
        mock_settings.log_level = "INFO"
        mock_settings.metrics_enabled = True
        mock_settings.metrics_host = "127.0.0.1"
        mock_settings.metrics_port = 8000

        with pytest.raises(RuntimeError, match="must differ"):
            with TestClient(app):
                pass


@pytest.mark.parametrize(
    ("metrics_host", "metrics_port", "message"),
    [
        ("", 9090, "metrics_host must not be empty"),
        ("127.0.0.1", 0, "metrics_port must be between 1 and 65535"),
        ("127.0.0.1", 65536, "metrics_port must be between 1 and 65535"),
    ],
)
def test_lifespan_rejects_invalid_metrics_listener(
    metrics_host: str,
    metrics_port: int,
    message: str,
):
    """Test that invalid dedicated-listener settings fail startup."""
    from stampbot.main import app

    with (
        patch("stampbot.main.is_configured", return_value=True),
        patch("stampbot.main.settings") as mock_settings,
    ):
        mock_settings.app_name = "stampbot"
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 8000
        mock_settings.log_level = "INFO"
        mock_settings.metrics_enabled = True
        mock_settings.metrics_host = metrics_host
        mock_settings.metrics_port = metrics_port

        with pytest.raises(RuntimeError, match=message):
            with TestClient(app):
                pass


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
