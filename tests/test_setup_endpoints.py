"""Tests for setup endpoints."""

import json
from html import unescape
from html.parser import HTMLParser
from unittest.mock import AsyncMock, patch


def configure_setup_settings(
    mock_settings,
    *,
    enabled: bool,
    base_url: str = "",
    allow_configured: bool = False,
) -> None:
    """Configure a mocked Dynaconf object for setup route tests."""
    values = {
        "setup_enabled": enabled,
        "setup_allow_configured": allow_configured,
        "base_url": base_url,
    }
    mock_settings.get.side_effect = lambda key, default=None: values.get(key, default)


class SetupPageParser(HTMLParser):
    """Extract structured setup page values for assertions."""

    def __init__(self) -> None:
        """Initialize parser state."""
        super().__init__()
        self.manifest_json = ""
        self.code_texts: list[str] = []
        self._in_code = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle HTML start tags."""
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "manifest":
            self.manifest_json = attributes.get("value") or ""
        elif tag == "code":
            self._in_code = True

    def handle_endtag(self, tag: str) -> None:
        """Handle HTML end tags."""
        if tag == "code":
            self._in_code = False

    def handle_data(self, data: str) -> None:
        """Handle text data."""
        if self._in_code:
            self.code_texts.append(data)


def parse_setup_page(html: str) -> tuple[dict[str, object], list[str]]:
    """Parse setup page HTML into manifest data and visible code snippets.

    Args:
        html: Setup page HTML.

    Returns:
        Parsed manifest JSON and code snippets.
    """
    parser = SetupPageParser()
    parser.feed(html)
    return json.loads(unescape(parser.manifest_json)), parser.code_texts


class TestSetupEndpointsConfigured:
    """Tests for setup endpoints when app is configured."""

    def test_root_returns_status_when_configured(self, test_client):
        """Test root returns app status when configured."""
        response = test_client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["app"] == "stampbot"
        assert data["status"] == "running"

    def test_setup_auto_closes_when_configured(self):
        """Test an initial-setup opt-in does not keep setup open after configuration."""
        with (
            patch("stampbot.main.is_configured", return_value=True),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(
                mock_settings,
                enabled=True,
                base_url="https://configured.example.test",
            )

            from fastapi.testclient import TestClient

            from stampbot.main import app

            response = TestClient(app, raise_server_exceptions=False).get("/setup")

        assert response.status_code == 403
        assert "already configured" in response.json()["detail"].lower()

    def test_setup_can_be_deliberately_reopened(self):
        """Test configured setup requires the second explicit opt-in."""
        with (
            patch("stampbot.main.is_configured", return_value=True),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(
                mock_settings,
                enabled=True,
                base_url="https://configured.example.test",
                allow_configured=True,
            )

            from fastapi.testclient import TestClient

            from stampbot.main import app

            response = TestClient(app, raise_server_exceptions=False).get("/setup")

        assert response.status_code == 200
        manifest, _ = parse_setup_page(response.text)
        assert manifest["redirect_url"] == "https://configured.example.test/setup/callback"

    def test_setup_status_is_closed_and_does_not_disclose_app_id(self):
        """Test setup status follows the configured-state gate."""
        with (
            patch("stampbot.main.is_configured", return_value=True),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=True)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            response = TestClient(app, raise_server_exceptions=False).get("/setup/status")

        assert response.status_code == 403
        assert "app_id" not in response.text

    def test_setup_callback_does_not_exchange_code_after_configuration(self):
        """Test the configured-state gate runs before any credential exchange."""
        with (
            patch("stampbot.main.is_configured", return_value=True),
            patch("stampbot.main.settings") as mock_settings,
            patch(
                "stampbot.main.exchange_code_for_credentials",
                new_callable=AsyncMock,
            ) as exchange,
        ):
            configure_setup_settings(mock_settings, enabled=True)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            response = TestClient(app, raise_server_exceptions=False).get(
                "/setup/callback?code=unused"
            )

        assert response.status_code == 403
        exchange.assert_not_awaited()


class TestSetupEndpointsUnconfigured:
    """Tests for setup endpoints when app is not configured."""

    def test_root_redirects_to_setup_when_unconfigured(self):
        """Test root redirects to /setup when not configured."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=True)
            mock_settings.app_name = "stampbot"
            mock_settings.host = "0.0.0.0"
            mock_settings.port = 8000
            mock_settings.log_level = "INFO"

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/", follow_redirects=False)

            assert response.status_code == 307
            assert response.headers["location"] == "/setup"

    def test_setup_shows_wizard_when_unconfigured(self):
        """Test /setup shows setup wizard page when not configured."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(
                mock_settings,
                enabled=True,
                base_url="http://localhost:8000",
            )

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/setup", headers={"Host": "localhost:8000"})

            assert response.status_code == 200
            assert "Stampbot Setup" in response.text
            assert "Create GitHub App" in response.text
            assert "Pull requests: Read" in response.text
            assert "Members: Read-only" in response.text
            assert "Administration: Read-only" in response.text
            assert "/webhook" in response.text  # Shown in instructions
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

    def test_setup_requires_configured_base_url(self):
        """Test request headers cannot supply a missing trusted setup URL."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=True)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/setup",
                headers={
                    "Host": "internal-service:8000",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "untrusted.example.test",
                },
            )

            assert response.status_code == 503
            assert "STAMPBOT_BASE_URL" in response.json()["detail"]
            assert "untrusted.example.test" not in response.text

    def test_setup_uses_configured_base_url(self):
        """Test /setup uses configured base_url over headers."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(
                mock_settings,
                enabled=True,
                base_url="https://configured.example.test/",
            )

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/setup",
                headers={
                    "Host": "internal-service:8000",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "untrusted.example.test",
                },
            )

            assert response.status_code == 200
            manifest, code_texts = parse_setup_page(response.text)
            assert manifest["redirect_url"] == "https://configured.example.test/setup/callback"
            hook_attributes = manifest["hook_attributes"]
            assert isinstance(hook_attributes, dict)
            assert hook_attributes["url"] == "https://configured.example.test/webhook"
            assert code_texts == ["https://configured.example.test/webhook"]
            assert "untrusted.example.test" not in response.text

    def test_setup_rejects_invalid_configured_base_url(self):
        """Test setup fails clearly for an invalid operator-configured URL."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(
                mock_settings,
                enabled=True,
                base_url="http://public.example.test",
            )

            from fastapi.testclient import TestClient

            from stampbot.main import app

            response = TestClient(app, raise_server_exceptions=False).get("/setup")

        assert response.status_code == 503
        assert "trusted public URL" in response.json()["detail"]

    def test_setup_status_omits_app_id(self):
        """Test active setup status exposes no App identifier."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=True)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            response = TestClient(app, raise_server_exceptions=False).get("/setup/status")

        assert response.status_code == 200
        assert response.json() == {"configured": False, "setup_enabled": True}
        assert "app_id" not in response.text

    def test_webhook_returns_503_when_unconfigured(self):
        """Test webhook returns 503 when not configured."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=True)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/webhook", json={})

            assert response.status_code == 503
            assert "not configured" in response.json()["detail"].lower()


class TestSetupCallback:
    """Tests for setup callback endpoint."""

    def test_callback_exchanges_code(self, test_client):
        """Test callback exchanges code for credentials."""
        mock_credentials = {
            "id": 12345,
            "pem": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            "webhook_secret": "test-secret",
            "slug": "test-stampbot",
            "name": "Test Stampbot",
        }

        with (
            patch("stampbot.main._require_setup_access"),
            patch(
                "stampbot.main.exchange_code_for_credentials",
                new_callable=AsyncMock,
                return_value=mock_credentials,
            ),
        ):
            response = test_client.get("/setup/callback?code=test-code")

            assert response.status_code == 200
            assert "Setup Complete" in response.text
            assert "12345" in response.text  # App ID
            assert "test-secret" in response.text  # Webhook secret

    def test_callback_shows_env_vars(self, test_client):
        """Test callback shows environment variables."""
        mock_credentials = {
            "id": 99999,
            "pem": "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
            "webhook_secret": "my-secret",
            "slug": "stampbot",
            "name": "Stampbot",
        }

        with (
            patch("stampbot.main._require_setup_access"),
            patch(
                "stampbot.main.exchange_code_for_credentials",
                new_callable=AsyncMock,
                return_value=mock_credentials,
            ),
        ):
            response = test_client.get("/setup/callback?code=abc")

            assert response.status_code == 200
            assert "STAMPBOT_APP_ID=99999" in response.text
            assert "STAMPBOT_WEBHOOK_SECRET=my-secret" in response.text
            assert "STAMPBOT_PRIVATE_KEY=" in response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"

    def test_callback_handles_exchange_error(self, test_client):
        """Test callback handles exchange error gracefully."""
        with (
            patch("stampbot.main._require_setup_access"),
            patch(
                "stampbot.main.exchange_code_for_credentials",
                new_callable=AsyncMock,
                side_effect=Exception("API error"),
            ),
        ):
            response = test_client.get("/setup/callback?code=bad-code")

            assert response.status_code == 500

    def test_callback_escapes_all_github_returned_values(self, test_client):
        """Test GitHub-returned text cannot inject callback HTML or attributes."""
        mock_credentials = {
            "id": '7</pre><script id="id-payload">bad()</script>',
            "pem": (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                '</pre><script id="key-payload">bad()</script>\n'
                "-----END RSA PRIVATE KEY-----"
            ),
            "webhook_secret": '</pre><script id="secret-payload">bad()</script>',
            "slug": 'stampbot" onclick="bad()',
            "name": '<img src=x onerror="bad()">',
        }

        with (
            patch("stampbot.main._require_setup_access"),
            patch(
                "stampbot.main.exchange_code_for_credentials",
                new_callable=AsyncMock,
                return_value=mock_credentials,
            ),
        ):
            response = test_client.get("/setup/callback?code=abc")

        assert response.status_code == 200
        assert '<script id="id-payload">' not in response.text
        assert '<script id="secret-payload">' not in response.text
        assert '<script id="key-payload">' not in response.text
        assert '<img src=x onerror="bad()">' not in response.text
        assert 'onclick="bad()"' not in response.text
        assert "&lt;script" in response.text
        assert "stampbot%22%20onclick%3D%22bad%28%29" in response.text


class TestSetupDisabled:
    """Tests for setup when disabled."""

    def test_setup_returns_403_when_disabled(self):
        """Test /setup returns 403 when setup is disabled."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=False)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/setup")

            assert response.status_code == 403

    def test_setup_callback_returns_403_when_disabled(self):
        """Test /setup/callback returns 403 when setup is disabled."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            configure_setup_settings(mock_settings, enabled=False)

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/setup/callback?code=test")

            assert response.status_code == 403
            assert "disabled" in response.json()["detail"].lower()
