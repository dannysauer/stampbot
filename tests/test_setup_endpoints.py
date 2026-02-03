"""Tests for setup endpoints."""

from unittest.mock import AsyncMock, patch


class TestSetupEndpointsConfigured:
    """Tests for setup endpoints when app is configured."""

    def test_root_returns_status_when_configured(self, test_client):
        """Test root returns app status when configured."""
        response = test_client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["app"] == "stampbot"
        assert data["status"] == "running"

    def test_setup_shows_already_configured(self, test_client):
        """Test /setup shows already configured message."""
        response = test_client.get("/setup")

        assert response.status_code == 200
        assert "Already Configured" in response.text

    def test_setup_status_shows_configured(self, test_client):
        """Test /setup/status shows configured status."""
        response = test_client.get("/setup/status")

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True


class TestSetupEndpointsUnconfigured:
    """Tests for setup endpoints when app is not configured."""

    def test_root_redirects_to_setup_when_unconfigured(self):
        """Test root redirects to /setup when not configured."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            mock_settings.setup_enabled = True
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
            mock_settings.setup_enabled = True
            mock_settings.get.return_value = ""  # No base_url override

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/setup", headers={"Host": "localhost:8000"})

            assert response.status_code == 200
            assert "Stampbot Setup" in response.text
            assert "Create GitHub App" in response.text
            assert "Pull requests: Read" in response.text
            assert "/webhook" in response.text  # Shown in instructions

    def test_setup_uses_forwarded_headers(self):
        """Test /setup uses X-Forwarded-Proto and X-Forwarded-Host headers."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            mock_settings.setup_enabled = True
            mock_settings.get.return_value = ""  # No base_url override

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/setup",
                headers={
                    "Host": "internal-service:8000",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "stampbot.example.com",
                },
            )

            assert response.status_code == 200
            # The manifest URL should use the forwarded host, not the internal Host header
            # Check for URL-encoded format which appears in the manifest URL
            assert "https%3A//stampbot.example.com" in response.text
            assert "internal-service" not in response.text

    def test_setup_uses_configured_base_url(self):
        """Test /setup uses configured base_url over headers."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            mock_settings.setup_enabled = True
            mock_settings.get.return_value = "https://configured.example.com"

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/setup",
                headers={
                    "Host": "internal-service:8000",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "forwarded.example.com",
                },
            )

            assert response.status_code == 200
            # Configured base_url takes priority over forwarded headers
            # Check for URL-encoded format which appears in the manifest URL
            assert "https%3A//configured.example.com" in response.text
            assert "forwarded.example.com" not in response.text
            assert "internal-service" not in response.text

    def test_webhook_returns_503_when_unconfigured(self):
        """Test webhook returns 503 when not configured."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            mock_settings.setup_enabled = True

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

        with patch(
            "stampbot.main.exchange_code_for_credentials",
            new_callable=AsyncMock,
            return_value=mock_credentials,
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

        with patch(
            "stampbot.main.exchange_code_for_credentials",
            new_callable=AsyncMock,
            return_value=mock_credentials,
        ):
            response = test_client.get("/setup/callback?code=abc")

            assert response.status_code == 200
            assert "STAMPBOT_APP_ID=99999" in response.text
            assert "STAMPBOT_WEBHOOK_SECRET=my-secret" in response.text
            assert "STAMPBOT_PRIVATE_KEY=" in response.text

    def test_callback_handles_exchange_error(self, test_client):
        """Test callback handles exchange error gracefully."""
        with patch(
            "stampbot.main.exchange_code_for_credentials",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            response = test_client.get("/setup/callback?code=bad-code")

            assert response.status_code == 500


class TestSetupDisabled:
    """Tests for setup when disabled."""

    def test_setup_returns_403_when_disabled(self):
        """Test /setup returns 403 when setup is disabled."""
        with (
            patch("stampbot.main.is_configured", return_value=False),
            patch("stampbot.main.settings") as mock_settings,
        ):
            mock_settings.setup_enabled = False

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/setup")

            assert response.status_code == 403

    def test_setup_callback_returns_403_when_disabled(self):
        """Test /setup/callback returns 403 when setup is disabled."""
        with patch("stampbot.main.settings") as mock_settings:
            mock_settings.setup_enabled = False

            from fastapi.testclient import TestClient

            from stampbot.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/setup/callback?code=test")

            assert response.status_code == 403
            assert "not allowed" in response.json()["detail"].lower()
