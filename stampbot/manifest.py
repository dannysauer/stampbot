# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""GitHub App manifest creation and setup flow."""

import json
import urllib.parse
from typing import Any
from urllib.parse import urlparse

import httpx

from stampbot.logger import get_logger

logger = get_logger(__name__)


def _validate_url(url: str, name: str) -> None:
    """Validate that a URL is well-formed and uses HTTPS (or localhost).

    Args:
        url: URL to validate
        name: Name of the URL parameter for error messages

    Raises:
        ValueError: If URL is invalid or doesn't use HTTPS
    """
    parsed = urlparse(url)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid {name}: must be a complete URL")

    # Allow http only for localhost (development)
    if parsed.scheme == "http":
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(f"Invalid {name}: must use HTTPS (HTTP only allowed for localhost)")
    elif parsed.scheme != "https":
        raise ValueError(f"Invalid {name}: must use HTTPS")


def validate_base_url(base_url: str) -> str:
    """Validate and normalize the trusted public URL used by setup.

    The setup flow must receive this value from operator configuration, never
    from request headers. Paths are allowed for deployments mounted below a
    reverse-proxy prefix, but credentials, query strings, fragments, and
    whitespace are not valid parts of the public base URL.

    Args:
        base_url: Operator-configured public URL.

    Returns:
        The normalized URL without a trailing slash.

    Raises:
        ValueError: If the URL is incomplete, unsafe, or unsupported.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Invalid base_url: must be configured")

    normalized = base_url.strip().rstrip("/")
    if any(
        character.isspace() or ord(character) < 32 or character in {"<", ">", '"', "'", "\\"}
        for character in normalized
    ):
        raise ValueError("Invalid base_url: contains an unsafe character")

    _validate_url(normalized, "base_url")
    parsed = urlparse(normalized)

    if parsed.hostname is None:
        raise ValueError("Invalid base_url: hostname is required")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Invalid base_url: must not contain credentials")
    if "?" in normalized or "#" in normalized:
        raise ValueError("Invalid base_url: must not contain a query or fragment")

    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Invalid base_url: port is invalid") from error

    return normalized


# GitHub App Manifest URLs
GITHUB_MANIFEST_URL = "https://github.com/settings/apps/new"
GITHUB_MANIFEST_CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"
GITHUB_MANIFEST_TIMEOUT_SECONDS = 10.0

# Required permissions for stampbot
MANIFEST_PERMISSIONS = {
    "pull_requests": "write",
    "contents": "read",
    "metadata": "read",
    "issues": "read",
    "members": "read",  # Required for team membership checks
    "administration": "read",  # Required for checking collaborator permissions
}

# Events stampbot subscribes to
MANIFEST_EVENTS = [
    "pull_request",
    "pull_request_review_comment",
    "issue_comment",
]


def create_manifest(
    redirect_url: str,
    webhook_url: str | None = None,
    app_name: str = "Stampbot",
    app_description: str = "GitHub PR Auto-Approval Bot",
) -> dict[str, Any]:
    """Generate GitHub App manifest with required permissions.

    Args:
        redirect_url: URL to redirect after app creation
        webhook_url: Full URL for webhook endpoint (e.g., https://example.com/webhook).
            If not provided, GitHub will prompt the user during app installation.
        app_name: Display name for the GitHub App
        app_description: Description for the GitHub App

    Returns:
        GitHub App manifest dictionary

    Raises:
        ValueError: If redirect_url is invalid or doesn't use HTTPS (localhost excepted)
    """
    _validate_url(redirect_url, "redirect_url")

    # Extract base URL from redirect URL
    base_url = redirect_url.rsplit("/setup/callback", 1)[0]

    manifest: dict[str, Any] = {
        "name": app_name,
        "description": app_description,
        "url": base_url,
        "redirect_url": redirect_url,
        "public": False,
        "default_permissions": MANIFEST_PERMISSIONS,
        "default_events": MANIFEST_EVENTS,
    }

    # Only include webhook URL if provided; GitHub will prompt for it otherwise
    if webhook_url:
        _validate_url(webhook_url, "webhook_url")
        manifest["hook_attributes"] = {
            "url": webhook_url,
            "active": True,
        }

    return manifest


def get_manifest_url(manifest: dict[str, Any]) -> str:
    """Generate the GitHub manifest creation URL.

    Args:
        manifest: The app manifest dictionary

    Returns:
        Full URL to redirect user for app creation
    """
    manifest_json = json.dumps(manifest)
    encoded_manifest = urllib.parse.quote(manifest_json)
    return f"{GITHUB_MANIFEST_URL}?manifest={encoded_manifest}"


async def exchange_code_for_credentials(code: str) -> dict[str, Any]:
    """Exchange the temporary code for app credentials.

    After a user creates a GitHub App from a manifest, GitHub redirects
    to the callback URL with a temporary code. This function exchanges
    that code for the actual app credentials.

    Args:
        code: Temporary code from GitHub callback

    Returns:
        Dictionary with app credentials:
        - id: App ID
        - pem: Private key (PEM format)
        - webhook_secret: Webhook secret
        - slug: App slug
        - name: App name
        - client_id: OAuth client ID
        - client_secret: OAuth client secret

    Raises:
        httpx.HTTPStatusError: If API call fails
        ValueError: If code contains invalid characters
    """
    # Validate code to prevent SSRF - GitHub codes are alphanumeric
    if not code or not code.isalnum():
        raise ValueError("Invalid manifest code format")

    url = GITHUB_MANIFEST_CONVERSION_URL.format(code=code)

    logger.info("Exchanging manifest code for credentials")

    async with httpx.AsyncClient(timeout=GITHUB_MANIFEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        credentials: dict[str, Any] = response.json()

    logger.info(
        f"Successfully created GitHub App: {credentials.get('name')} (ID: {credentials.get('id')})"
    )

    return credentials
