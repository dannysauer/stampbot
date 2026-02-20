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

# Valid permission keys and their allowed values, sourced from GitHub's official
# OpenAPI spec (components/schemas/app-permissions).
# https://github.com/github/rest-api-description
VALID_PERMISSIONS: dict[str, frozenset[str]] = {
    "actions": frozenset({"read", "write"}),
    "administration": frozenset({"read", "write"}),
    "artifact_metadata": frozenset({"read", "write"}),
    "attestations": frozenset({"read", "write"}),
    "checks": frozenset({"read", "write"}),
    "codespaces": frozenset({"read", "write"}),
    "contents": frozenset({"read", "write"}),
    "custom_properties_for_organizations": frozenset({"read", "write"}),
    "dependabot_secrets": frozenset({"read", "write"}),
    "deployments": frozenset({"read", "write"}),
    "discussions": frozenset({"read", "write"}),
    "email_addresses": frozenset({"read", "write"}),
    "enterprise_custom_properties_for_organizations": frozenset({"read", "write", "admin"}),
    "environments": frozenset({"read", "write"}),
    "followers": frozenset({"read", "write"}),
    "git_ssh_keys": frozenset({"read", "write"}),
    "gpg_keys": frozenset({"read", "write"}),
    "interaction_limits": frozenset({"read", "write"}),
    "issues": frozenset({"read", "write"}),
    "members": frozenset({"read", "write"}),
    "merge_queues": frozenset({"read", "write"}),
    "metadata": frozenset({"read", "write"}),
    "organization_administration": frozenset({"read", "write"}),
    "organization_announcement_banners": frozenset({"read", "write"}),
    "organization_copilot_seat_management": frozenset({"write"}),
    "organization_custom_org_roles": frozenset({"read", "write"}),
    "organization_custom_properties": frozenset({"read", "write", "admin"}),
    "organization_custom_roles": frozenset({"read", "write"}),
    "organization_events": frozenset({"read"}),
    "organization_hooks": frozenset({"read", "write"}),
    "organization_packages": frozenset({"read", "write"}),
    "organization_personal_access_token_requests": frozenset({"read", "write"}),
    "organization_personal_access_tokens": frozenset({"read", "write"}),
    "organization_plan": frozenset({"read"}),
    "organization_projects": frozenset({"read", "write", "admin"}),
    "organization_secrets": frozenset({"read", "write"}),
    "organization_self_hosted_runners": frozenset({"read", "write"}),
    "organization_user_blocking": frozenset({"read", "write"}),
    "packages": frozenset({"read", "write"}),
    "pages": frozenset({"read", "write"}),
    "profile": frozenset({"write"}),
    "pull_requests": frozenset({"read", "write"}),
    "repository_custom_properties": frozenset({"read", "write"}),
    "repository_hooks": frozenset({"read", "write"}),
    "repository_projects": frozenset({"read", "write", "admin"}),
    "secret_scanning_alerts": frozenset({"read", "write"}),
    "secrets": frozenset({"read", "write"}),
    "security_events": frozenset({"read", "write"}),
    "single_file": frozenset({"read", "write"}),
    "starring": frozenset({"read", "write"}),
    "statuses": frozenset({"read", "write"}),
    "vulnerability_alerts": frozenset({"read", "write"}),
    "workflows": frozenset({"write"}),
}

# Valid webhook event names for GitHub Apps, sourced from GitHub's official
# OpenAPI spec (components/schemas/webhook-* app.events enums).
VALID_EVENTS: frozenset[str] = frozenset({
    "branch_protection_rule",
    "check_run",
    "check_suite",
    "code_scanning_alert",
    "commit_comment",
    "content_reference",
    "create",
    "delete",
    "deploy_key",
    "deployment",
    "deployment_review",
    "deployment_status",
    "discussion",
    "discussion_comment",
    "fork",
    "gollum",
    "issue_comment",
    "issues",
    "label",
    "member",
    "membership",
    "merge_group",
    "merge_queue_entry",
    "milestone",
    "org_block",
    "organization",
    "page_build",
    "project",
    "project_card",
    "project_column",
    "projects_v2_item",
    "public",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "pull_request_review_thread",
    "push",
    "registry_package",
    "release",
    "reminder",
    "repository",
    "repository_dispatch",
    "repository_import",
    "secret_scanning_alert",
    "secret_scanning_alert_location",
    "security_and_analysis",
    "star",
    "status",
    "team",
    "team_add",
    "watch",
    "workflow_dispatch",
    "workflow_job",
    "workflow_run",
})


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Validate manifest permissions and events against GitHub's official schema.

    Args:
        manifest: The app manifest dictionary to validate

    Raises:
        ValueError: If any permission key, permission value, or event name is
            not recognised by GitHub's API.
    """
    for key, value in manifest.get("default_permissions", {}).items():
        if key not in VALID_PERMISSIONS:
            raise ValueError(f"Unknown permission key: '{key}'")
        if value not in VALID_PERMISSIONS[key]:
            allowed = sorted(VALID_PERMISSIONS[key])
            raise ValueError(
                f"Invalid value '{value}' for permission '{key}'. Allowed: {allowed}"
            )

    for event in manifest.get("default_events", []):
        if event not in VALID_EVENTS:
            raise ValueError(f"Unknown event: '{event}'")


# Required permissions for stampbot
MANIFEST_PERMISSIONS = {
    "pull_requests": "write",
    "contents": "read",
    "metadata": "read",
    "issues": "read",
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

    validate_manifest(manifest)
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
