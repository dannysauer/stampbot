"""Tests for GitHub App manifest setup flow."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from stampbot.manifest import (
    MANIFEST_EVENTS,
    MANIFEST_PERMISSIONS,
    create_manifest,
    exchange_code_for_credentials,
    get_manifest_url,
)


class TestCreateManifest:
    """Tests for manifest creation."""

    def test_manifest_contains_required_permissions(self):
        """Test manifest contains required permissions."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["default_permissions"] == MANIFEST_PERMISSIONS
        assert manifest["default_permissions"]["pull_requests"] == "write"
        assert manifest["default_permissions"]["contents"] == "read"
        assert manifest["default_permissions"]["metadata"] == "read"

    def test_manifest_contains_required_events(self):
        """Test manifest contains required events."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["default_events"] == MANIFEST_EVENTS
        assert "pull_request" in manifest["default_events"]
        assert "pull_request_review_comment" in manifest["default_events"]
        assert "issue_comment" in manifest["default_events"]

    def test_manifest_webhook_url(self):
        """Test manifest contains correct webhook URL."""
        manifest = create_manifest(
            webhook_url="https://stampbot.example.com/webhook",
            redirect_url="https://stampbot.example.com/setup/callback",
        )

        assert manifest["hook_attributes"]["url"] == "https://stampbot.example.com/webhook"
        assert manifest["hook_attributes"]["active"] is True

    def test_manifest_redirect_url(self):
        """Test manifest contains correct redirect URL."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["redirect_url"] == "https://example.com/setup/callback"

    def test_manifest_base_url(self):
        """Test manifest contains correct base URL."""
        manifest = create_manifest(
            webhook_url="https://stampbot.example.com/webhook",
            redirect_url="https://stampbot.example.com/setup/callback",
        )

        assert manifest["url"] == "https://stampbot.example.com"

    def test_manifest_is_private(self):
        """Test manifest creates private app."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["public"] is False

    def test_manifest_custom_name(self):
        """Test manifest with custom app name."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/setup/callback",
            app_name="My Custom Stampbot",
            app_description="Custom description",
        )

        assert manifest["name"] == "My Custom Stampbot"
        assert manifest["description"] == "Custom description"


class TestGetManifestUrl:
    """Tests for manifest URL generation."""

    def test_url_starts_with_github(self):
        """Test URL starts with GitHub manifest creation URL."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/callback",
        )
        url = get_manifest_url(manifest)

        assert url.startswith("https://github.com/settings/apps/new?manifest=")

    def test_url_contains_encoded_manifest(self):
        """Test URL contains URL-encoded manifest."""
        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/callback",
        )
        url = get_manifest_url(manifest)

        # URL should contain encoded quotes from JSON
        assert "%22" in url  # URL encoded double quote

    def test_url_is_valid_json_when_decoded(self):
        """Test manifest can be decoded from URL."""
        import urllib.parse

        manifest = create_manifest(
            webhook_url="https://example.com/webhook",
            redirect_url="https://example.com/callback",
        )
        url = get_manifest_url(manifest)

        # Extract and decode the manifest parameter
        encoded_manifest = url.split("manifest=")[1]
        decoded_manifest = urllib.parse.unquote(encoded_manifest)
        parsed_manifest = json.loads(decoded_manifest)

        assert parsed_manifest["hook_attributes"]["url"] == "https://example.com/webhook"


class TestExchangeCode:
    """Tests for code exchange."""

    @pytest.mark.asyncio
    async def test_exchange_code_success(self):
        """Test successful code exchange."""
        import httpx

        mock_response_data = {
            "id": 12345,
            "pem": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            "webhook_secret": "test-secret",
            "slug": "my-stampbot",
            "name": "My Stampbot",
            "client_id": "client123",
            "client_secret": "secret456",
        }

        # Create a proper mock response with request attached
        mock_request = httpx.Request(
            "POST", "https://api.github.com/app-manifests/test/conversions"
        )
        mock_response = httpx.Response(200, json=mock_response_data, request=mock_request)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await exchange_code_for_credentials("test-code")

            assert result["id"] == 12345
            assert "pem" in result
            assert "webhook_secret" in result
            assert result["slug"] == "my-stampbot"

    @pytest.mark.asyncio
    async def test_exchange_code_calls_correct_url(self):
        """Test code exchange calls correct GitHub API URL."""
        import httpx

        mock_request = httpx.Request(
            "POST", "https://api.github.com/app-manifests/test/conversions"
        )
        mock_response = httpx.Response(
            200, json={"id": 1, "pem": "", "webhook_secret": ""}, request=mock_request
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await exchange_code_for_credentials("my-test-code")

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "my-test-code" in call_args[0][0]
            assert "app-manifests" in call_args[0][0]
