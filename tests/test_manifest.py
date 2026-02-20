"""Tests for GitHub App manifest setup flow."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from stampbot.manifest import (
    MANIFEST_EVENTS,
    MANIFEST_PERMISSIONS,
    VALID_EVENTS,
    VALID_PERMISSIONS,
    create_manifest,
    exchange_code_for_credentials,
    get_manifest_url,
    validate_manifest,
)


class TestUrlValidation:
    """Tests for URL validation in manifest creation."""

    def test_rejects_http_non_localhost_redirect(self):
        """Test that HTTP redirect URLs for non-localhost are rejected."""
        import pytest

        with pytest.raises(ValueError, match="must use HTTPS"):
            create_manifest(
                redirect_url="http://example.com/setup/callback",
            )

    def test_allows_http_localhost(self):
        """Test that HTTP URLs for localhost are allowed."""
        manifest = create_manifest(
            redirect_url="http://localhost:8000/setup/callback",
            webhook_url="http://localhost:8000/webhook",
        )
        assert manifest["hook_attributes"]["url"] == "http://localhost:8000/webhook"

    def test_rejects_invalid_redirect_url(self):
        """Test that invalid redirect URLs are rejected."""
        import pytest

        with pytest.raises(ValueError, match="must be a complete URL"):
            create_manifest(
                redirect_url="not-a-url",
            )

    def test_rejects_non_https_scheme(self):
        """Test that non-HTTPS schemes are rejected for webhook URLs."""
        import pytest

        with pytest.raises(ValueError, match="must use HTTPS"):
            create_manifest(
                redirect_url="https://example.com/setup/callback",
                webhook_url="ftp://example.com/webhook",
            )


class TestValidateManifest:
    """Tests for manifest schema validation."""

    def test_manifest_permissions_are_valid(self):
        """All MANIFEST_PERMISSIONS keys and values are in the official schema."""
        for key, value in MANIFEST_PERMISSIONS.items():
            assert key in VALID_PERMISSIONS, f"Unknown permission key: '{key}'"
            assert value in VALID_PERMISSIONS[key], (
                f"Invalid value '{value}' for permission '{key}'"
            )

    def test_manifest_events_are_valid(self):
        """All MANIFEST_EVENTS names are in the official schema."""
        for event in MANIFEST_EVENTS:
            assert event in VALID_EVENTS, f"Unknown event: '{event}'"

    def test_validate_manifest_rejects_unknown_permission_key(self):
        """validate_manifest raises ValueError for an unrecognised permission key."""
        with pytest.raises(ValueError, match="Unknown permission key"):
            validate_manifest({"default_permissions": {"nonexistent_perm": "read"}})

    def test_validate_manifest_rejects_invalid_permission_value(self):
        """validate_manifest raises ValueError for an invalid permission level."""
        with pytest.raises(ValueError, match="Invalid value"):
            validate_manifest({"default_permissions": {"contents": "admin"}})

    def test_validate_manifest_rejects_unknown_event(self):
        """validate_manifest raises ValueError for an unrecognised event name."""
        with pytest.raises(ValueError, match="Unknown event"):
            validate_manifest({"default_events": ["not_a_real_event"]})

    def test_validate_manifest_accepts_valid_manifest(self):
        """validate_manifest passes for a well-formed manifest."""
        validate_manifest(
            {
                "default_permissions": MANIFEST_PERMISSIONS,
                "default_events": MANIFEST_EVENTS,
            }
        )


class TestCreateManifest:
    """Tests for manifest creation."""

    def test_manifest_contains_required_permissions(self):
        """Test manifest contains required permissions."""
        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["default_permissions"] == MANIFEST_PERMISSIONS
        assert manifest["default_permissions"]["pull_requests"] == "write"
        assert manifest["default_permissions"]["contents"] == "read"
        assert manifest["default_permissions"]["metadata"] == "read"
        assert manifest["default_permissions"]["issues"] == "read"

    def test_manifest_contains_required_events(self):
        """Test manifest contains required events."""
        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["default_events"] == MANIFEST_EVENTS
        assert "pull_request" in manifest["default_events"]
        assert "pull_request_review_comment" in manifest["default_events"]
        assert "issue_comment" in manifest["default_events"]

    def test_manifest_webhook_url_when_provided(self):
        """Test manifest contains webhook URL when provided."""
        manifest = create_manifest(
            redirect_url="https://stampbot.example.com/setup/callback",
            webhook_url="https://stampbot.example.com/webhook",
        )

        assert manifest["hook_attributes"]["url"] == "https://stampbot.example.com/webhook"
        assert manifest["hook_attributes"]["active"] is True

    def test_manifest_no_webhook_url_when_omitted(self):
        """Test manifest omits hook_attributes when webhook_url not provided."""
        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )

        assert "hook_attributes" not in manifest

    def test_manifest_redirect_url(self):
        """Test manifest contains correct redirect URL."""
        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["redirect_url"] == "https://example.com/setup/callback"

    def test_manifest_base_url(self):
        """Test manifest contains correct base URL derived from redirect URL."""
        manifest = create_manifest(
            redirect_url="https://stampbot.example.com/setup/callback",
        )

        assert manifest["url"] == "https://stampbot.example.com"

    def test_manifest_is_private(self):
        """Test manifest creates private app."""
        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )

        assert manifest["public"] is False

    def test_manifest_custom_name(self):
        """Test manifest with custom app name."""
        manifest = create_manifest(
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
            redirect_url="https://example.com/setup/callback",
        )
        url = get_manifest_url(manifest)

        assert url.startswith("https://github.com/settings/apps/new?manifest=")

    def test_url_contains_encoded_manifest(self):
        """Test URL contains URL-encoded manifest."""
        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )
        url = get_manifest_url(manifest)

        # URL should contain encoded quotes from JSON
        assert "%22" in url  # URL encoded double quote

    def test_url_is_valid_json_when_decoded(self):
        """Test manifest can be decoded from URL."""
        import urllib.parse

        manifest = create_manifest(
            redirect_url="https://example.com/setup/callback",
        )
        url = get_manifest_url(manifest)

        # Extract and decode the manifest parameter
        encoded_manifest = url.split("manifest=")[1]
        decoded_manifest = urllib.parse.unquote(encoded_manifest)
        parsed_manifest = json.loads(decoded_manifest)

        assert parsed_manifest["redirect_url"] == "https://example.com/setup/callback"


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
            "POST", "https://api.github.com/app-manifests/testcode123/conversions"
        )
        mock_response = httpx.Response(200, json=mock_response_data, request=mock_request)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await exchange_code_for_credentials("testcode123")

            assert result["id"] == 12345
            assert "pem" in result
            assert "webhook_secret" in result
            assert result["slug"] == "my-stampbot"

    @pytest.mark.asyncio
    async def test_exchange_code_calls_correct_url(self):
        """Test code exchange calls correct GitHub API URL."""
        import httpx

        mock_request = httpx.Request(
            "POST", "https://api.github.com/app-manifests/mytestcode/conversions"
        )
        mock_response = httpx.Response(
            200, json={"id": 1, "pem": "", "webhook_secret": ""}, request=mock_request
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await exchange_code_for_credentials("mytestcode")

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "mytestcode" in call_args[0][0]
            assert "app-manifests" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_exchange_code_rejects_empty_code(self):
        """Test that empty code raises ValueError."""
        with pytest.raises(ValueError, match="Invalid manifest code format"):
            await exchange_code_for_credentials("")

    @pytest.mark.asyncio
    async def test_exchange_code_rejects_invalid_characters(self):
        """Test that code with special characters raises ValueError to prevent SSRF."""
        with pytest.raises(ValueError, match="Invalid manifest code format"):
            await exchange_code_for_credentials("../../../etc/passwd")

        with pytest.raises(ValueError, match="Invalid manifest code format"):
            await exchange_code_for_credentials("code?param=value")

        with pytest.raises(ValueError, match="Invalid manifest code format"):
            await exchange_code_for_credentials("code/path")

    @pytest.mark.asyncio
    async def test_exchange_code_accepts_valid_alphanumeric(self):
        """Test that valid alphanumeric codes are accepted."""
        import httpx

        mock_request = httpx.Request(
            "POST", "https://api.github.com/app-manifests/abc123/conversions"
        )
        mock_response = httpx.Response(
            200, json={"id": 1, "pem": "", "webhook_secret": ""}, request=mock_request
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            # Valid alphanumeric codes should work
            await exchange_code_for_credentials("abc123XYZ")
            mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# Live tests — require real network access to api.github.com.
# Excluded from the default run; execute with: pytest -m live
# ---------------------------------------------------------------------------

_GITHUB_OPENAPI_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description"
    "/main/descriptions/api.github.com/api.github.com.json"
)


def _fetch_live_schema() -> dict:
    """Download GitHub's OpenAPI spec and return the parsed JSON."""
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(_GITHUB_OPENAPI_URL)
        response.raise_for_status()
        return response.json()


def _extract_live_permissions(spec: dict) -> dict[str, set[str]]:
    """Return {permission_key: {allowed_values}} from the live OpenAPI spec."""
    props = (
        spec.get("components", {})
        .get("schemas", {})
        .get("app-permissions", {})
        .get("properties", {})
    )
    return {key: set(val.get("enum", [])) for key, val in props.items()}


def _extract_live_events(spec: dict) -> set[str]:
    """Return the union of all webhook event name enums from the live OpenAPI spec."""
    live_events: set[str] = set()

    def _walk(obj: object) -> None:
        if isinstance(obj, dict):
            items = obj.get("items", {})
            if isinstance(items, dict):
                enum = items.get("enum", [])
                if "pull_request" in enum:  # only event-name enums contain this
                    live_events.update(enum)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(spec)
    return live_events


@pytest.mark.live
class TestLiveSchemaValidation:
    """Validate our manifest constants against GitHub's live OpenAPI spec.

    These tests make real HTTPS requests to raw.githubusercontent.com and are
    excluded from the default pytest run.  Run them explicitly in CI with::

        pytest -m live
    """

    @pytest.fixture(scope="class")
    def live_spec(self):
        """Fetch and cache the GitHub OpenAPI spec for the whole class."""
        return _fetch_live_schema()

    @pytest.fixture(scope="class")
    def live_permissions(self, live_spec):
        return _extract_live_permissions(live_spec)

    @pytest.fixture(scope="class")
    def live_events(self, live_spec):
        return _extract_live_events(live_spec)

    def test_manifest_permission_keys_still_valid(self, live_permissions):
        """Every key in MANIFEST_PERMISSIONS exists in the live GitHub schema."""
        for key in MANIFEST_PERMISSIONS:
            assert key in live_permissions, (
                f"Permission key '{key}' in MANIFEST_PERMISSIONS is no longer "
                f"recognised by GitHub's API. Remove or update it."
            )

    def test_manifest_permission_values_still_valid(self, live_permissions):
        """Every value in MANIFEST_PERMISSIONS is still an allowed level."""
        for key, value in MANIFEST_PERMISSIONS.items():
            if key in live_permissions:
                assert value in live_permissions[key], (
                    f"Permission level '{value}' for '{key}' is no longer valid. "
                    f"Allowed: {sorted(live_permissions[key])}"
                )

    def test_manifest_events_still_valid(self, live_events):
        """Every event in MANIFEST_EVENTS is still recognised by GitHub."""
        for event in MANIFEST_EVENTS:
            assert event in live_events, (
                f"Event '{event}' in MANIFEST_EVENTS is no longer recognised "
                f"by GitHub's API. Remove or update it."
            )

    def test_valid_permissions_constant_has_no_stale_keys(self, live_permissions):
        """VALID_PERMISSIONS contains no keys that GitHub has removed."""
        stale = set(VALID_PERMISSIONS) - set(live_permissions)
        assert not stale, (
            f"These keys in VALID_PERMISSIONS no longer exist in GitHub's schema "
            f"and should be removed: {sorted(stale)}"
        )

    def test_valid_events_constant_has_no_stale_entries(self, live_events):
        """VALID_EVENTS contains no event names that GitHub has removed."""
        stale = VALID_EVENTS - live_events
        assert not stale, (
            f"These events in VALID_EVENTS no longer exist in GitHub's schema "
            f"and should be removed: {sorted(stale)}"
        )
