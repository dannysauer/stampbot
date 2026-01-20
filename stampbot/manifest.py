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


# GitHub App Manifest URLs
GITHUB_MANIFEST_URL = "https://github.com/settings/apps/new"
GITHUB_MANIFEST_CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"
GITHUB_MANIFEST_TIMEOUT_SECONDS = 10.0

# Required permissions for stampbot
MANIFEST_PERMISSIONS = {
    "pull_requests": "write",
    "contents": "read",
    "metadata": "read",
}

# Events stampbot subscribes to
MANIFEST_EVENTS = [
    "pull_request",
    "pull_request_review_comment",
    "issue_comment",
]


def create_manifest(
    webhook_url: str,
    redirect_url: str,
    app_name: str = "Stampbot",
    app_description: str = "GitHub PR Auto-Approval Bot",
) -> dict[str, Any]:
    """Generate GitHub App manifest with required permissions.

    Args:
        webhook_url: Full URL for webhook endpoint (e.g., https://example.com/webhook)
        redirect_url: URL to redirect after app creation
        app_name: Display name for the GitHub App
        app_description: Description for the GitHub App

    Returns:
        GitHub App manifest dictionary

    Raises:
        ValueError: If URLs are invalid or don't use HTTPS
    """
    # Validate URLs to prevent SSRF and other attacks
    _validate_url(webhook_url, "webhook_url")
    _validate_url(redirect_url, "redirect_url")

    # Extract base URL from webhook URL
    base_url = webhook_url.rsplit("/webhook", 1)[0]

    return {
        "name": app_name,
        "description": app_description,
        "url": base_url,
        "hook_attributes": {
            "url": webhook_url,
            "active": True,
        },
        "redirect_url": redirect_url,
        "public": False,
        "default_permissions": MANIFEST_PERMISSIONS,
        "default_events": MANIFEST_EVENTS,
    }


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
