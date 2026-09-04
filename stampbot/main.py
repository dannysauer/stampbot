# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Main FastAPI application."""

import html
import json
import re
import time
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog.contextvars
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from starlette.routing import Match

from stampbot.config import is_configured, settings
from stampbot.github_client import _sanitize_error
from stampbot.logger import configure_logging, get_logger
from stampbot.manifest import (
    GITHUB_MANIFEST_URL,
    create_manifest,
    exchange_code_for_credentials,
    validate_base_url,
)
from stampbot.metrics import (
    errors_total,
    http_request_duration_seconds,
    http_request_size_bytes,
    http_requests_in_progress,
    http_requests_total,
    http_response_size_bytes,
    set_app_info,
    start_metrics_server,
    stop_metrics_server,
    webhook_processing_duration_seconds,
    webhook_signature_validations_total,
)
from stampbot.telemetry import configure_telemetry, instrument_fastapi
from stampbot.version import APP_VERSION
from stampbot.webhook_handler import webhook_handler

# Configure logging
configure_logging()
logger = get_logger(__name__)

# Configure OpenTelemetry
configure_telemetry()

# Security limits
MAX_WEBHOOK_BODY_SIZE = 1024 * 1024  # 1MB - GitHub webhooks are typically much smaller
# X-GitHub-Delivery is a GUID such as 72d3162e-cc78-11e3-81ab-4c9367dc0958.
MAX_DELIVERY_ID_LENGTH = 64
DELIVERY_ID_PATTERN = re.compile(r"[A-Za-z0-9-]+")
UNMATCHED_ENDPOINT = "unmatched"
SETUP_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "base-uri 'none'; frame-ancestors 'none'; form-action https://github.com"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _setup_requested() -> bool:
    """Return whether the operator explicitly enabled setup."""
    return bool(settings.get("setup_enabled", False))


def _setup_allowed_when_configured() -> bool:
    """Return whether setup was explicitly reopened after configuration."""
    return bool(settings.get("setup_allow_configured", False))


def _setup_is_available(configured: bool | None = None) -> bool:
    """Return whether setup routes should be available for the current state."""
    if not _setup_requested():
        return False

    app_configured = is_configured() if configured is None else configured
    return not app_configured or _setup_allowed_when_configured()


def _require_setup_access() -> None:
    """Reject requests when the operator has not made setup available.

    Raises:
        HTTPException: If setup is disabled or automatically closed.
    """
    if not _setup_requested():
        raise HTTPException(status_code=403, detail="Setup is disabled")
    if is_configured() and not _setup_allowed_when_configured():
        raise HTTPException(
            status_code=403,
            detail="Setup is closed because Stampbot is already configured",
        )


def _trusted_setup_base_url() -> str:
    """Return the validated operator-configured setup base URL.

    Raises:
        HTTPException: If the trusted public URL is missing or invalid.
    """
    configured_base_url = settings.get("base_url", "")
    try:
        return validate_base_url(configured_base_url)
    except ValueError as error:
        logger.warning("Setup base URL is missing or invalid: %s", error)
        raise HTTPException(
            status_code=503,
            detail=("Setup requires STAMPBOT_BASE_URL to be set to Stampbot's trusted public URL"),
        ) from None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup and shutdown events.

    Args:
        app: FastAPI application instance.

    Yields:
        None after startup, resumes for shutdown.
    """
    metrics_runtime: tuple[Any, Any] | None = None

    # Startup
    logger.info(
        f"Starting {settings.app_name} on {settings.host}:{settings.port}",
        extra={
            "host": settings.host,
            "port": settings.port,
            "log_level": settings.log_level,
        },
    )

    # Log setup mode status
    configured = is_configured()
    if not configured:
        logger.warning("GitHub App credentials not configured.")
        if _setup_is_available(configured=False):
            logger.info("Setup is enabled; visit /setup to create your GitHub App")
        else:
            logger.warning("Setup is disabled; enable it explicitly to provision the App")
    else:
        logger.info("GitHub App credentials configured successfully")

    if settings.metrics_enabled:
        metrics_host = str(settings.metrics_host)
        metrics_port = int(settings.metrics_port)
        if not metrics_host.strip():
            raise RuntimeError("metrics_host must not be empty")
        if not 1 <= metrics_port <= 65535:
            raise RuntimeError("metrics_port must be between 1 and 65535")
        if metrics_port == int(settings.port):
            raise RuntimeError("metrics_port must differ from the public HTTP port")
        metrics_runtime = start_metrics_server(metrics_host, metrics_port)
        logger.info(
            "Prometheus metrics listener started",
            extra={"host": metrics_host, "port": metrics_port},
        )

    try:
        yield
    finally:
        if metrics_runtime is not None:
            stop_metrics_server(*metrics_runtime)
        # Shutdown
        logger.info("Shutting down stampbot")


# Create FastAPI app
app = FastAPI(
    title="Stampbot",
    description="GitHub PR Auto-Approval App",
    version=APP_VERSION,
    lifespan=lifespan,
)

# Instrument with OpenTelemetry
instrument_fastapi(app)

# Set app info metric
set_app_info(APP_VERSION)


def _metric_endpoint_label(request: Request) -> str:
    """Return a bounded Prometheus endpoint label for a request.

    Route templates are defined by the application, so labels such as
    ``/widgets/{widget_id}`` have bounded cardinality even when the path
    parameter is attacker controlled. Requests that do not match a registered
    route share one fallback label instead of exposing their raw URL paths.

    Args:
        request: Incoming HTTP request.

    Returns:
        The matched route template or the unmatched-route fallback.
    """
    current_route = request.scope.get("route")
    current_path = getattr(current_route, "path", None)
    if isinstance(current_path, str):
        return current_path

    partial_match: str | None = None
    for route in request.app.routes:
        match, child_scope = route.matches(request.scope)
        matched_route = child_scope.get("route", route)
        route_path = getattr(matched_route, "path", None)
        if not isinstance(route_path, str):
            continue
        if match is Match.FULL:
            return route_path
        if match is Match.PARTIAL and partial_match is None:
            # A path match with the wrong method will become a 405 response.
            partial_match = route_path

    return partial_match or UNMATCHED_ENDPOINT


@app.middleware("http")
async def metrics_middleware(request: Request, call_next: Any) -> Response:
    """Middleware to track HTTP metrics.

    Args:
        request: Incoming HTTP request.
        call_next: Next middleware or route handler.

    Returns:
        HTTP response from downstream handler.
    """
    method = request.method
    endpoint = _metric_endpoint_label(request)

    # Track in-progress requests
    http_requests_in_progress.labels(method=method, endpoint=endpoint).inc()

    start_time = time.time()

    try:
        response = await call_next(request)

        duration = time.time() - start_time

        # Track request metrics
        http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status=response.status_code,
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

        # Track request size (from Content-Length header if available)
        content_length = request.headers.get("content-length")
        if content_length:
            http_request_size_bytes.labels(
                method=method,
                endpoint=endpoint,
            ).observe(int(content_length))

        # Track response size
        response_size = response.headers.get("content-length")
        if response_size:
            http_response_size_bytes.labels(
                method=method,
                endpoint=endpoint,
            ).observe(int(response_size))

        return response  # type: ignore[no-any-return]

    finally:
        http_requests_in_progress.labels(method=method, endpoint=endpoint).dec()


class LoggingMiddleware:
    """Pure ASGI middleware that binds per-request context to structured log records.

    Unlike ``BaseHTTPMiddleware``, this calls the inner app directly in the
    same coroutine frame (``await self.app(scope, receive, send)``).  Stacking
    two ``BaseHTTPMiddleware`` layers causes the inner route-handler frames to
    run inside a nested anyio task group, which breaks ``sys.settrace``-based
    coverage collection.  A pure ASGI class avoids that nesting.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Process an ASGI event, binding client IP to the structlog context.

        For HTTP scopes the middleware clears any inherited structlog context,
        extracts the real client IP from the configured forwarding header (or
        the direct connection address as a fallback), and binds it so that
        every log record emitted during the request includes ``client_ip``.

        Non-HTTP scopes (lifespan, WebSocket) are passed through unchanged.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] == "http":
            structlog.contextvars.clear_contextvars()

            client_ip: str | None = None
            client_ip_header: str = settings.get("client_ip_header", "X-Forwarded-For")

            if client_ip_header:
                raw = Request(scope).headers.get(client_ip_header)
                if raw:
                    # X-Forwarded-For may be a comma-separated list; the leftmost
                    # entry is the original client (proxies append to the right).
                    client_ip = raw.split(",")[0].strip()

            if not client_ip and scope.get("client"):
                client_ip = scope["client"][0]

            if client_ip:
                structlog.contextvars.bind_contextvars(client_ip=client_ip)

        await self.app(scope, receive, send)


app.add_middleware(LoggingMiddleware)


@app.get("/")
async def root() -> Response:
    """Root endpoint - redirects to setup if not configured.

    Returns:
        Redirect to /setup if unconfigured, otherwise JSON status response.
    """
    configured = is_configured()
    if not configured and _setup_is_available(configured=False):
        return RedirectResponse(url="/setup", status_code=307)

    return JSONResponse(
        content={"app": "stampbot", "version": APP_VERSION, "status": "running"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check endpoint.

    A shallow signal that the process is up and able to serve requests; it does
    not check configuration. Used by the Kubernetes liveness probe, so it must
    stay cheap and return 200 whenever the app is running. Use /ready for a
    readiness signal that reflects whether Stampbot can actually serve webhooks.

    Returns:
        Dictionary with health status.
    """
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> Response:
    """Readiness check endpoint.

    Reports whether the pod should receive traffic. Unlike /health (a shallow
    liveness signal), readiness reflects whether Stampbot can serve its current
    purpose:

    - configured: ready to serve webhooks;
    - setup mode enabled: ready to serve the /setup flow so an operator can
      configure the app.

    Returning 503 while setup mode is enabled would remove the pod from the
    Service endpoints and make /setup unreachable through the Service/ingress —
    a configuration deadlock. So an unconfigured pod stays Ready as long as
    setup mode is on; only an unconfigured pod with setup disabled is "not
    ready", since it can neither serve webhooks nor be configured.

    Returns:
        JSON response with a per-check breakdown: 200 when ready, 503 otherwise.
    """
    configured = is_configured()
    setup_enabled = _setup_is_available(configured=configured)
    checks = {"configured": configured, "setup_enabled": setup_enabled}
    is_ready = configured or setup_enabled
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not ready", "checks": checks},
    )


def _delivery_id_label(raw: str | None) -> str | None:
    """Return a GitHub delivery GUID suitable for logs and span attributes.

    GitHub sends a 36-character GUID. Anything else is dropped rather than
    stored, since the value is only useful for matching GitHub's delivery log.

    Args:
        raw: Value of the ``X-GitHub-Delivery`` header, if present.

    Returns:
        The GUID, or None when the header is missing or malformed.
    """
    if not raw:
        return None
    if len(raw) > MAX_DELIVERY_ID_LENGTH or not DELIVERY_ID_PATTERN.fullmatch(raw):
        return None
    return raw


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(None, alias="X-GitHub-Delivery"),
) -> dict[str, Any]:
    """GitHub webhook endpoint.

    Args:
        request: FastAPI request.
        x_github_event: GitHub event type.
        x_hub_signature_256: Webhook signature.
        x_github_delivery: GitHub delivery GUID, bound to logs and traces after
            the signature is verified so a delivery in GitHub's log can be
            matched to Stampbot's records.

    Returns:
        Response dictionary with status and message.

    Raises:
        HTTPException: 400 if event header or JSON invalid, 401 if signature invalid,
            413 if body too large, 503 if not configured, 500 on internal error.
    """
    # Check if app is configured
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Stampbot not configured. Visit /setup to complete setup.",
        )

    if not x_github_event:
        errors_total.labels(error_type="missing_event").inc()
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    # Check content length before reading body to prevent memory exhaustion
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_WEBHOOK_BODY_SIZE:
        errors_total.labels(error_type="payload_too_large").inc()
        raise HTTPException(status_code=413, detail="Request body too large")

    # Get raw body for signature verification
    body = await request.body()

    # Double-check actual body size (in case content-length was missing/incorrect)
    if len(body) > MAX_WEBHOOK_BODY_SIZE:
        errors_total.labels(error_type="payload_too_large").inc()
        raise HTTPException(status_code=413, detail="Request body too large")

    # Verify signature
    if not webhook_handler.verify_signature(body, x_hub_signature_256):
        webhook_signature_validations_total.labels(result="invalid").inc()
        errors_total.labels(error_type="signature_invalid").inc()
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    webhook_signature_validations_total.labels(result="valid").inc()

    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception as e:
        errors_total.labels(error_type="payload_invalid").inc()
        logger.error("Failed to parse webhook payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    # The header is only trusted once the HMAC check has passed. Bound length
    # keeps a malformed value out of log storage.
    delivery_id = _delivery_id_label(x_github_delivery)
    if delivery_id:
        structlog.contextvars.bind_contextvars(delivery_id=delivery_id)

    # Handle event with timing
    try:
        start_time = time.time()
        result = await webhook_handler.handle_event(
            x_github_event, payload, delivery_id=delivery_id
        )
        duration = time.time() - start_time

        webhook_processing_duration_seconds.labels(event_type=x_github_event or "unknown").observe(
            duration
        )

        return result
    except Exception as e:
        errors_total.labels(error_type="webhook_handler_error").inc()
        sanitized = _sanitize_error(e)
        logger.error("Error handling webhook event: %s", sanitized, extra={"error": sanitized})
        raise HTTPException(status_code=500, detail="Internal server error") from None


# =============================================================================
# Setup Endpoints - GitHub App Manifest Flow
# =============================================================================


@app.get("/setup")
async def setup_page() -> Response:
    """Setup page with manifest creation button.

    Returns:
        HTML page with setup instructions and GitHub App creation button.

    Raises:
        HTTPException: If setup is disabled (403).
    """
    _require_setup_access()

    # This URL is operator-controlled configuration. Request Host and
    # X-Forwarded-* headers are deliberately never trusted for App manifests.
    base_url = _trusted_setup_base_url()
    redirect_url = f"{base_url}/setup/callback"
    webhook_url = f"{base_url}/webhook"

    manifest = create_manifest(redirect_url, webhook_url=webhook_url)
    # HTML-escape the JSON for safe embedding in the form
    manifest_json = html.escape(json.dumps(manifest))
    webhook_url_escaped = html.escape(webhook_url, quote=True)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Stampbot Setup</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   max-width: 600px; margin: 50px auto; padding: 20px; }}
            h1 {{ color: #24292e; }}
            .button {{ display: inline-block; padding: 12px 24px; background: #2ea44f;
                      color: white; text-decoration: none; border-radius: 6px; font-weight: 600;
                      border: none; cursor: pointer; font-size: 16px; }}
            .button:hover {{ background: #22863a; }}
            .info {{ background: #f6f8fa; padding: 16px; border-radius: 6px; margin: 20px 0; }}
            code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 3px; }}
            ul {{ margin: 10px 0; padding-left: 20px; }}
        </style>
    </head>
    <body>
        <h1>Stampbot Setup</h1>
        <p>Click the button below to create a new GitHub App with the required permissions.</p>

        <div class="info">
            <strong>Permissions that will be requested:</strong>
            <ul>
                <li>Pull requests: Read &amp; write</li>
                <li>Contents: Read-only</li>
                <li>Metadata: Read-only</li>
                <li>Issues: Read-only</li>
                <li>Members: Read-only</li>
                <li>Administration: Read-only</li>
            </ul>
            <strong>Events:</strong>
            <ul>
                <li>Pull request</li>
                <li>Pull request review comment</li>
                <li>Issue comment</li>
            </ul>
        </div>

        <form method="post" action="{GITHUB_MANIFEST_URL}">
            <input type="hidden" name="manifest" value="{manifest_json}">
            <button type="submit" class="button">Create GitHub App</button>
        </form>

        <div class="info">
            <p><strong>Note:</strong> The webhook URL will be automatically configured to:</p>
            <p><code>{webhook_url_escaped}</code></p>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content, headers=SETUP_HTML_HEADERS)


@app.get("/setup/callback")
async def setup_callback(code: str) -> Response:
    """Handle callback from GitHub after app creation.

    Args:
        code: Temporary code from GitHub to exchange for credentials.

    Returns:
        HTML page with credentials and setup instructions.

    Raises:
        HTTPException: If setup disabled (403) or code exchange fails (500).
    """
    _require_setup_access()

    try:
        credentials = await exchange_code_for_credentials(code)
    except Exception as e:
        logger.error("Failed to exchange code for credentials: %s", _sanitize_error(e))
        raise HTTPException(status_code=500, detail="Failed to complete setup") from None

    # Security note: Credentials are displayed in the HTML response for user convenience.
    # This mirrors GitHub's own manifest flow behavior. The tradeoffs are acceptable because:
    # 1. This is a one-time setup flow, not a recurring operation
    # 2. The page includes a warning to save credentials securely
    # 3. Users can immediately rotate the webhook secret if concerned
    # 4. The alternative (file download) adds friction without meaningful security gain
    #    since the credentials must be transmitted to the browser regardless
    private_key_escaped = html.escape(str(credentials["pem"]).replace("\n", "\\n"), quote=True)
    app_id_escaped = html.escape(str(credentials["id"]), quote=True)
    webhook_secret_escaped = html.escape(str(credentials["webhook_secret"]), quote=True)
    app_name_escaped = html.escape(str(credentials.get("name", "Stampbot")), quote=True)
    app_slug = urllib.parse.quote(str(credentials.get("slug", "stampbot")), safe="")
    installation_url = html.escape(
        f"https://github.com/settings/apps/{app_slug}/installations",
        quote=True,
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Stampbot Setup Complete</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   max-width: 800px; margin: 50px auto; padding: 20px; }}
            h1 {{ color: #2ea44f; }}
            h2 {{ color: #24292e; margin-top: 30px; }}
            .success {{ background: #dcffe4; border: 1px solid #34d058; padding: 16px;
                       border-radius: 6px; margin: 20px 0; }}
            .warning {{ background: #fffbdd; border: 1px solid #f9c513; padding: 16px;
                       border-radius: 6px; margin: 20px 0; }}
            pre {{ background: #24292e; color: #e1e4e8; padding: 16px; border-radius: 6px;
                  overflow-x: auto; white-space: pre-wrap; word-break: break-all;
                  font-size: 13px; }}
            .copy-btn {{ background: #0366d6; color: white; border: none; padding: 8px 16px;
                        border-radius: 4px; cursor: pointer; margin-top: 8px; }}
            .copy-btn:hover {{ background: #0256cc; }}
            code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 3px; }}
            ol {{ line-height: 1.8; }}
            a {{ color: #0366d6; }}
        </style>
    </head>
    <body>
        <h1>Setup Complete!</h1>

        <div class="success">
            <strong>GitHub App created successfully!</strong>
            <p>App Name: {app_name_escaped}</p>
            <p>App ID: {app_id_escaped}</p>
        </div>

        <div class="warning">
            <strong>Important:</strong> Save these credentials securely.
            The private key will not be shown again.
        </div>

        <h2>Environment Variables</h2>
        <p>Add these to your <code>.env</code> file or environment:</p>

        <pre id="env-vars">STAMPBOT_APP_ID={app_id_escaped}
STAMPBOT_WEBHOOK_SECRET={webhook_secret_escaped}
STAMPBOT_PRIVATE_KEY="{private_key_escaped}"</pre>

        <button class="copy-btn" onclick="copyEnv()">Copy to Clipboard</button>

        <h2>Kubernetes Secret</h2>
        <p>For Kubernetes deployment, create a secret with the private key in a file:</p>

        <pre>kubectl create secret generic stampbot-github \\
  --from-literal=STAMPBOT_APP_ID={app_id_escaped} \\
  --from-literal=STAMPBOT_WEBHOOK_SECRET={webhook_secret_escaped} \\
  --from-file=STAMPBOT_PRIVATE_KEY=private-key.pem \\
  -n stampbot</pre>

        <h2>Next Steps</h2>
        <ol>
            <li>Save the credentials above to your <code>.env</code> file</li>
            <li>Restart stampbot with the new credentials</li>
            <li><a href="{installation_url}" target="_blank" rel="noopener noreferrer">
                Install the app on your repositories</a></li>
        </ol>

        <script>
            function copyEnv() {{
                const text = document.getElementById('env-vars').innerText;
                navigator.clipboard.writeText(text).then(() => {{
                    alert('Copied to clipboard!');
                }});
            }}
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content, headers=SETUP_HTML_HEADERS)


@app.get("/setup/status")
async def setup_status() -> dict[str, Any]:
    """Check setup status.

    Returns:
        Dictionary with configuration status and settings.
    """
    _require_setup_access()
    return {
        "configured": is_configured(),
        "setup_enabled": True,
    }
