# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Main FastAPI application."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from stampbot.config import is_configured, settings
from stampbot.logger import configure_logging, get_logger
from stampbot.manifest import create_manifest, exchange_code_for_credentials, get_manifest_url
from stampbot.metrics import (
    errors_total,
    get_metrics,
    http_request_duration_seconds,
    http_request_size_bytes,
    http_requests_in_progress,
    http_requests_total,
    http_response_size_bytes,
    set_app_info,
    webhook_processing_duration_seconds,
    webhook_signature_validations_total,
)
from stampbot.telemetry import configure_telemetry, instrument_fastapi
from stampbot.webhook_handler import webhook_handler

# Configure logging
configure_logging()
logger = get_logger(__name__)

# Configure OpenTelemetry
configure_telemetry()

APP_VERSION = "0.1.0"

# Security limits
MAX_WEBHOOK_BODY_SIZE = 1024 * 1024  # 1MB - GitHub webhooks are typically much smaller


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup and shutdown events.

    Args:
        app: FastAPI application instance.

    Yields:
        None after startup, resumes for shutdown.
    """
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
    if not is_configured():
        logger.warning("GitHub App credentials not configured. Running in setup mode.")
        logger.info("Visit /setup to create your GitHub App")
    else:
        logger.info("GitHub App credentials configured successfully")

    yield
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
    endpoint = request.url.path

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


@app.get("/")
async def root() -> Response:
    """Root endpoint - redirects to setup if not configured.

    Returns:
        Redirect to /setup if unconfigured, otherwise JSON status response.
    """
    if not is_configured() and settings.setup_enabled:
        return RedirectResponse(url="/setup", status_code=307)

    return Response(
        content='{"app": "stampbot", "version": "' + APP_VERSION + '", "status": "running"}',
        media_type="application/json",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        Dictionary with health status.
    """
    return {"status": "healthy"}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint.

    Returns:
        Plain text response with Prometheus metrics.
    """
    return PlainTextResponse(
        content=get_metrics().decode("utf-8"),
        media_type="text/plain",
    )


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """GitHub webhook endpoint.

    Args:
        request: FastAPI request.
        x_github_event: GitHub event type.
        x_hub_signature_256: Webhook signature.

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

    # Handle event with timing
    try:
        start_time = time.time()
        result = await webhook_handler.handle_event(x_github_event, payload)
        duration = time.time() - start_time

        webhook_processing_duration_seconds.labels(event_type=x_github_event or "unknown").observe(
            duration
        )

        return result
    except Exception as e:
        errors_total.labels(error_type="webhook_handler_error").inc()
        logger.error("Error handling webhook event: %s", e, extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Internal server error") from None


# =============================================================================
# Setup Endpoints - GitHub App Manifest Flow
# =============================================================================


@app.get("/setup")
async def setup_page(request: Request) -> Response:
    """Setup page with manifest creation button.

    Args:
        request: FastAPI request for URL detection.

    Returns:
        HTML page with setup instructions and GitHub App creation button.

    Raises:
        HTTPException: If setup is disabled (403).
    """
    if not settings.setup_enabled:
        raise HTTPException(status_code=403, detail="Setup not allowed in this environment")

    if is_configured():
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
            <head><title>Stampbot - Already Configured</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                       max-width: 600px; margin: 50px auto; padding: 20px; }
                h1 { color: #24292e; }
                .info { background: #dcffe4; border: 1px solid #34d058; padding: 16px;
                        border-radius: 6px; }
            </style>
            </head>
            <body>
                <h1>Stampbot Already Configured</h1>
                <div class="info">
                    <p>Your GitHub App credentials are already configured.</p>
                    <p>Stampbot is ready to receive webhooks.</p>
                </div>
            </body>
            </html>
            """,
            status_code=200,
        )

    # Determine base URL from request Host header
    base_url = str(request.base_url).rstrip("/")
    redirect_url = f"{base_url}/setup/callback"

    # Don't include webhook_url - GitHub will prompt the user for it during installation
    manifest = create_manifest(redirect_url)
    manifest_url = get_manifest_url(manifest)

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
                      color: white; text-decoration: none; border-radius: 6px; font-weight: 600; }}
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
            </ul>
            <strong>Events:</strong>
            <ul>
                <li>Pull request</li>
                <li>Pull request review comment</li>
                <li>Issue comment</li>
            </ul>
        </div>

        <a href="{manifest_url}" class="button">Create GitHub App</a>

        <div class="info">
            <p><strong>Note:</strong> GitHub will prompt you for the webhook URL.</p>
            <p>Use your public URL with <code>/webhook</code> path.</p>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@app.get("/setup/callback")
async def setup_callback(request: Request, code: str) -> Response:
    """Handle callback from GitHub after app creation.

    Args:
        request: FastAPI request.
        code: Temporary code from GitHub to exchange for credentials.

    Returns:
        HTML page with credentials and setup instructions.

    Raises:
        HTTPException: If setup disabled (403) or code exchange fails (500).
    """
    if not settings.setup_enabled:
        raise HTTPException(status_code=403, detail="Setup not allowed")

    try:
        credentials = await exchange_code_for_credentials(code)
    except Exception as e:
        logger.error("Failed to exchange code for credentials: %s", e)
        raise HTTPException(status_code=500, detail="Failed to complete setup") from None

    # Security note: Credentials are displayed in the HTML response for user convenience.
    # This mirrors GitHub's own manifest flow behavior. The tradeoffs are acceptable because:
    # 1. This is a one-time setup flow, not a recurring operation
    # 2. The page includes a warning to save credentials securely
    # 3. Users can immediately rotate the webhook secret if concerned
    # 4. The alternative (file download) adds friction without meaningful security gain
    #    since the credentials must be transmitted to the browser regardless
    private_key_escaped = credentials["pem"].replace("\n", "\\n")
    app_slug = credentials.get("slug", "stampbot")

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
            <p>App Name: {credentials.get("name", "Stampbot")}</p>
            <p>App ID: {credentials["id"]}</p>
        </div>

        <div class="warning">
            <strong>Important:</strong> Save these credentials securely.
            The private key will not be shown again.
        </div>

        <h2>Environment Variables</h2>
        <p>Add these to your <code>.env</code> file or environment:</p>

        <pre id="env-vars">STAMPBOT_APP_ID={credentials["id"]}
STAMPBOT_WEBHOOK_SECRET={credentials["webhook_secret"]}
STAMPBOT_PRIVATE_KEY="{private_key_escaped}"</pre>

        <button class="copy-btn" onclick="copyEnv()">Copy to Clipboard</button>

        <h2>Kubernetes Secret</h2>
        <p>For Kubernetes deployment, create a secret with the private key in a file:</p>

        <pre>kubectl create secret generic stampbot-github \\
  --from-literal=STAMPBOT_APP_ID={credentials["id"]} \\
  --from-literal=STAMPBOT_WEBHOOK_SECRET={credentials["webhook_secret"]} \\
  --from-file=STAMPBOT_PRIVATE_KEY=private-key.pem \\
  -n stampbot</pre>

        <h2>Next Steps</h2>
        <ol>
            <li>Save the credentials above to your <code>.env</code> file</li>
            <li>Restart stampbot with the new credentials</li>
            <li><a href="https://github.com/settings/apps/{app_slug}/installations" target="_blank">
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

    return HTMLResponse(content=html_content)


@app.get("/setup/status")
async def setup_status() -> dict[str, Any]:
    """Check setup status.

    Returns:
        Dictionary with configuration status and settings.
    """
    return {
        "configured": is_configured(),
        "setup_enabled": settings.setup_enabled,
        "app_id": settings.app_id if is_configured() else None,
    }
