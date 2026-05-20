# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Tests for GitHub client module."""

from unittest.mock import Mock, patch

import pytest

TEST_PEM_KEY = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
TEST_TOKEN = "test-token"  # noqa: S105


class TestGitHubAppClientInit:
    """Tests for GitHubAppClient initialization."""

    def test_init_creates_uninitialized_client(self):
        """Test that __init__ creates an uninitialized client."""
        from stampbot.github_client import GitHubAppClient

        client = GitHubAppClient()
        assert client._auth is None
        assert client._integration is None
        assert client._initialized is False


class TestEnsureInitialized:
    """Tests for _ensure_initialized method."""

    def test_ensure_initialized_when_already_initialized(self):
        """Test that _ensure_initialized returns early if already initialized."""
        from stampbot.github_client import GitHubAppClient

        client = GitHubAppClient()
        client._initialized = True

        # Should return without doing anything
        client._ensure_initialized()
        assert client._initialized is True

    def test_ensure_initialized_raises_when_not_configured(self):
        """Test that _ensure_initialized raises when app not configured."""
        with patch("stampbot.github_client.is_configured", return_value=False):
            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(RuntimeError, match="GitHub App not configured"):
                client._ensure_initialized()

    def test_ensure_initialized_raises_when_app_id_none(self):
        """Test that _ensure_initialized raises when app_id is None."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
        ):
            mock_settings.app_id = None

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(RuntimeError, match="App ID not configured"):
                client._ensure_initialized()

    def test_ensure_initialized_success(self):
        """Test successful initialization."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth") as mock_auth,
            patch("stampbot.github_client.GithubIntegration") as mock_integration,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            client._ensure_initialized()

            assert client._initialized is True
            mock_auth.assert_called_once()
            mock_integration.assert_called_once()


class TestIntegrationProperty:
    """Tests for integration property."""

    def test_integration_raises_when_not_initialized(self):
        """Test that integration property raises when not initialized after _ensure_initialized."""
        with patch("stampbot.github_client.is_configured", return_value=False):
            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(RuntimeError):
                _ = client.integration


class TestLoadPrivateKey:
    """Tests for _load_private_key method."""

    def test_load_private_key_raises_when_none(self):
        """Test that _load_private_key raises when key is None."""
        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = None

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(RuntimeError, match="Private key not configured"):
                client._load_private_key()

    def test_load_private_key_returns_pem_directly(self):
        """Test that PEM content is returned directly."""
        pem_key = TEST_PEM_KEY
        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = pem_key

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client._load_private_key()
            assert result == pem_key

    def test_load_private_key_reads_from_file(self, tmp_path):
        """Test that private key is read from file when path provided."""
        pem_key = TEST_PEM_KEY
        key_file = tmp_path / "private-key.pem"
        key_file.write_text(pem_key)

        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = str(key_file)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client._load_private_key()
            assert result == pem_key

    def test_load_private_key_raises_on_file_error(self):
        """Test that _load_private_key raises when file doesn't exist."""
        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = "/nonexistent/path/to/key.pem"

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(ValueError, match="Invalid private key path"):
                client._load_private_key()

    def test_load_private_key_raises_on_read_error(self, tmp_path):
        """Test that _load_private_key raises when file read fails."""
        key_file = tmp_path / "private-key.pem"
        key_file.write_text("dummy")

        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = str(key_file)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()

            # Mock open to raise an exception after the file existence check passes
            with patch("builtins.open", side_effect=PermissionError("Access denied")):
                with pytest.raises(PermissionError, match="Access denied"):
                    client._load_private_key()

    def test_load_private_key_raises_on_invalid_pem_format(self, tmp_path):
        """Test that _load_private_key raises when file content is not PEM format."""
        key_file = tmp_path / "private-key.pem"
        key_file.write_text("this is not a valid PEM key")

        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = str(key_file)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(ValueError, match="Private key must be in PEM format"):
                client._load_private_key()


class TestGetInstallationClient:
    """Tests for _get_installation_client method."""

    def test_get_installation_client_success(self):
        """Test successful installation client creation."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client._get_installation_client(123456)

            mock_github.assert_called_once()
            assert result is not None

    def test_get_installation_client_failure(self):
        """Test installation client creation failure."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_integration.get_access_token.side_effect = Exception("Token exchange failed")
            mock_integration_cls.return_value = mock_integration

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(Exception, match="Token exchange failed"):
                client._get_installation_client(123456)


class TestUpdateRateLimitMetrics:
    """Tests for _update_rate_limit_metrics method."""

    def test_update_rate_limit_metrics_success(self):
        """Test successful rate limit metrics update."""
        from stampbot.github_client import GitHubAppClient

        client = GitHubAppClient()

        mock_github_client = Mock()
        mock_rate_limit = Mock()
        mock_rate_limit.core.remaining = 4500
        mock_rate_limit.core.limit = 5000
        mock_github_client.get_rate_limit.return_value = mock_rate_limit

        # Should not raise
        client._update_rate_limit_metrics(mock_github_client, 123456)

    def test_update_rate_limit_metrics_handles_exception(self):
        """Test that rate limit metrics errors are silently ignored."""
        from stampbot.github_client import GitHubAppClient

        client = GitHubAppClient()

        mock_github_client = Mock()
        mock_github_client.get_rate_limit.side_effect = Exception("API Error")

        # Should not raise - errors are silently ignored
        client._update_rate_limit_metrics(mock_github_client, 123456)


class TestApprovePR:
    """Tests for approve_pr method."""

    def test_approve_pr_success(self):
        """Test successful PR approval."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_pr = Mock()
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.approve_pr(123456, "owner/repo", 42)

            assert result is True
            mock_pr.create_review.assert_called_once()

    def test_approve_pr_failure(self):
        """Test PR approval failure."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_pr = Mock()
            mock_pr.create_review.side_effect = Exception("API Error")
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.approve_pr(123456, "owner/repo", 42)

            assert result is False


class TestGetPRHeadSHA:
    """Tests for get_pr_head_sha method."""

    def test_get_pr_head_sha_success(self):
        """Test successful PR head SHA lookup."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_pr = Mock()
            mock_pr.head.sha = "current-head"
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_pr_head_sha(123456, "owner/repo", 42)

            assert result == "current-head"

    def test_get_pr_head_sha_returns_none_on_error(self):
        """Test PR head SHA lookup returns None on API errors."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_github = Mock()
            mock_github.get_repo.side_effect = Exception("API Error")
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_pr_head_sha(123456, "owner/repo", 42)

            assert result is None


class TestDismissApproval:
    """Tests for dismiss_approval method."""

    def test_dismiss_approval_success(self):
        """Test successful approval dismissal."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_review = Mock()
            mock_pr = Mock()
            mock_pr.get_review.return_value = mock_review
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.dismiss_approval(123456, "owner/repo", 42, 999)

            assert result is True
            mock_review.dismiss.assert_called_once()

    def test_dismiss_approval_failure(self):
        """Test approval dismissal failure."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_review = Mock()
            mock_review.dismiss.side_effect = Exception("API Error")
            mock_pr = Mock()
            mock_pr.get_review.return_value = mock_review
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.dismiss_approval(123456, "owner/repo", 42, 999)

            assert result is False


class TestGetRepoFile:
    """Tests for get_repo_file method."""

    def test_get_repo_file_success(self):
        """Test successful file retrieval."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_content = Mock()
            mock_content.decoded_content = b"file content"
            mock_repo = Mock()
            mock_repo.get_contents.return_value = mock_content
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_repo_file(123456, "owner/repo", "stampbot.toml")

            assert result == "file content"

    def test_get_repo_file_returns_none_for_directory(self):
        """Test that get_repo_file returns None when path is a directory."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            # Return a list (indicating directory)
            mock_repo = Mock()
            mock_repo.get_contents.return_value = [Mock(), Mock()]
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_repo_file(123456, "owner/repo", "some/directory")

            assert result is None

    def test_get_repo_file_returns_none_on_error(self):
        """Test that get_repo_file returns None when file not found."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_contents.side_effect = Exception("File not found")
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_repo_file(123456, "owner/repo", "nonexistent.toml")

            assert result is None


class TestFindBotReviews:
    """Tests for find_bot_reviews method."""

    def test_find_bot_reviews_success(self):
        """Test successful bot review finding."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration.get_app.return_value = Mock(slug="stampbot")
            mock_integration_cls.return_value = mock_integration

            # Create mock reviews
            bot_review = Mock()
            bot_review.user.login = "stampbot[bot]"
            bot_review.state = "APPROVED"
            bot_review.id = 123

            other_review = Mock()
            other_review.user.login = "other-user"
            other_review.state = "APPROVED"
            other_review.id = 456

            mock_pr = Mock()
            mock_pr.get_reviews.return_value = [bot_review, other_review]
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.find_bot_reviews(123456, "owner/repo", 42)

            assert result == [123]

    def test_find_bot_reviews_returns_empty_on_error(self):
        """Test that find_bot_reviews returns empty list on error."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_github = Mock()
            mock_github.get_repo.side_effect = Exception("API Error")
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.find_bot_reviews(123456, "owner/repo", 42)

            assert result == []


class TestFindBotApprovalReviews:
    """Tests for find_bot_approval_reviews method."""

    def test_find_bot_approval_reviews_success(self):
        """Test successful bot approval review state lookup."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration.get_app.return_value = Mock(slug="stampbot")
            mock_integration_cls.return_value = mock_integration

            bot_approved_review = Mock()
            bot_approved_review.user.login = "stampbot[bot]"
            bot_approved_review.state = "APPROVED"
            bot_approved_review.id = 123
            bot_approved_review.commit_id = "headsha"

            bot_dismissed_review = Mock()
            bot_dismissed_review.user.login = "stampbot[bot]"
            bot_dismissed_review.state = "DISMISSED"
            bot_dismissed_review.id = 124
            bot_dismissed_review.commit_id = "oldsha"

            bot_commented_review = Mock()
            bot_commented_review.user.login = "stampbot[bot]"
            bot_commented_review.state = "COMMENTED"
            bot_commented_review.id = 125

            other_review = Mock()
            other_review.user.login = "other-user"
            other_review.state = "APPROVED"
            other_review.id = 456

            mock_pr = Mock()
            mock_pr.get_reviews.return_value = [
                bot_approved_review,
                bot_dismissed_review,
                bot_commented_review,
                other_review,
            ]
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.find_bot_approval_reviews(123456, "owner/repo", 42)

            assert result == [
                {"id": 123, "state": "APPROVED", "commit_id": "headsha"},
                {"id": 124, "state": "DISMISSED", "commit_id": "oldsha"},
            ]

    def test_find_bot_approval_reviews_returns_empty_on_error(self):
        """Test that find_bot_approval_reviews returns empty list on error."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_github = Mock()
            mock_github.get_repo.side_effect = Exception("API Error")
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.find_bot_approval_reviews(123456, "owner/repo", 42)

            assert result == []


class TestUserHasPermission:
    """Tests for user_has_permission method."""

    def test_user_has_permission_true(self):
        """Test required permission satisfied returns True."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_collaborator_permission.return_value = "write"
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=1, limit=2))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.user_has_permission(123456, "owner/repo", "alice", "write")

            assert result is True

    def test_user_has_permission_false(self):
        """Test insufficient permission returns False."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_collaborator_permission.return_value = "read"
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=1, limit=2))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.user_has_permission(123456, "owner/repo", "bob", "write")

            assert result is False

    def test_user_has_permission_error(self):
        """Test permission check returns False on error."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_collaborator_permission.side_effect = Exception("API Error")
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.user_has_permission(123456, "owner/repo", "carol", "maintain")

            assert result is False

    def test_user_has_permission_invalid_levels(self):
        """Test invalid permission levels return False."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_collaborator_permission.return_value = "unknown"
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=1, limit=2))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.user_has_permission(123456, "owner/repo", "dana", "unknown")

            assert result is False


class TestGetUserTeamSlugs:
    """Tests for get_user_team_slugs method."""

    def test_get_user_team_slugs_success(self):
        """Test successful team membership check."""

        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            # Mock user
            mock_user = Mock()
            mock_user.login = "alice"

            # Mock team that has alice as member
            mock_team = Mock()
            mock_team.has_in_members.return_value = True

            # Mock org
            mock_org = Mock()
            mock_org.get_team_by_slug.return_value = mock_team

            mock_github = Mock()
            mock_github.get_organization.return_value = mock_org
            mock_github.get_user.return_value = mock_user
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_user_team_slugs(
                123456, "acme", "alice", ["release-team", "deploy-team"]
            )

            assert result == ["release-team", "deploy-team"]
            assert mock_org.get_team_by_slug.call_count == 2

    def test_get_user_team_slugs_with_org_prefix(self):
        """Test team membership check with org/team format."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_user = Mock()
            mock_team = Mock()
            mock_team.has_in_members.return_value = True
            mock_org = Mock()
            mock_org.get_team_by_slug.return_value = mock_team

            mock_github = Mock()
            mock_github.get_organization.return_value = mock_org
            mock_github.get_user.return_value = mock_user
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_user_team_slugs(123456, "acme", "alice", ["acme/release-team"])

            # Should extract "release-team" from "acme/release-team"
            mock_org.get_team_by_slug.assert_called_with("release-team")
            assert result == ["release-team"]

    def test_get_user_team_slugs_not_member(self):
        """Test team membership check when user is not a member."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_user = Mock()
            mock_team = Mock()
            mock_team.has_in_members.return_value = False  # Not a member
            mock_org = Mock()
            mock_org.get_team_by_slug.return_value = mock_team

            mock_github = Mock()
            mock_github.get_organization.return_value = mock_org
            mock_github.get_user.return_value = mock_user
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_user_team_slugs(123456, "acme", "alice", ["release-team"])

            assert result == []

    def test_get_user_team_slugs_team_not_found(self):
        """Test team membership check when team doesn't exist."""
        from github.GithubException import GithubException

        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_org = Mock()
            mock_org.get_team_by_slug.side_effect = GithubException(
                404, {"message": "Not Found"}, None
            )

            mock_github = Mock()
            mock_github.get_organization.return_value = mock_org
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=4500, limit=5000))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_user_team_slugs(123456, "acme", "alice", ["nonexistent-team"])

            # Should return empty list but not raise
            assert result == []

    def test_get_user_team_slugs_general_exception(self):
        """Test team membership check with general exception."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_github = Mock()
            mock_github.get_organization.side_effect = Exception("Network error")
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.get_user_team_slugs(123456, "acme", "alice", ["release-team"])

            # Should return empty list on error
            assert result == []


class TestRepoHasLabel:
    """Tests for repo_has_label method."""

    def test_repo_has_label_true(self):
        """Test repo_has_label returns True when label exists."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_label.return_value = Mock()
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=1, limit=2))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.repo_has_label(123456, "owner/repo", "autoapprove")

            assert result is True

    def test_repo_has_label_not_found(self):
        """Test repo_has_label returns False when label is missing."""
        from github.GithubException import GithubException

        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_label.side_effect = GithubException(404, {"message": "Not Found"}, None)
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.repo_has_label(123456, "owner/repo", "missing")

            assert result is False

    def test_repo_has_label_github_exception(self):
        """Test repo_has_label returns None on GitHubException errors."""
        from github.GithubException import GithubException

        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_label.side_effect = GithubException(500, {"message": "Error"}, None)
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.repo_has_label(123456, "owner/repo", "autoapprove")

            assert result is None

    def test_repo_has_label_error(self):
        """Test repo_has_label returns None on error."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_label.side_effect = Exception("API Error")
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.repo_has_label(123456, "owner/repo", "autoapprove")

            assert result is None


class TestCreatePrReviewComment:
    """Tests for create_pr_review_comment method."""

    def test_create_pr_review_comment_success(self):
        """Test successful review comment creation."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_pr = Mock()
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=1, limit=2))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.create_pr_review_comment(
                123456,
                "owner/repo",
                42,
                "Config error",
            )

            assert result is True
            mock_pr.create_review.assert_called_once()

    def test_create_pr_review_comment_failure(self):
        """Test review comment creation handles errors."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_pull.side_effect = Exception("API Error")
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.create_pr_review_comment(
                123456,
                "owner/repo",
                42,
                "Config error",
            )

            assert result is False


class TestCreateIssueComment:
    """Tests for create_issue_comment method."""

    def test_create_issue_comment_success(self):
        """Test successful issue comment creation."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_issue = Mock()
            mock_repo = Mock()
            mock_repo.get_issue.return_value = mock_issue
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_rate_limit.return_value = Mock(core=Mock(remaining=1, limit=2))
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.create_issue_comment(
                123456,
                "owner/repo",
                42,
                "Help text",
            )

            assert result is True
            mock_repo.get_issue.assert_called_once_with(42)
            mock_issue.create_comment.assert_called_once_with("Help text")

    def test_create_issue_comment_failure(self):
        """Test issue comment creation handles errors."""
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch("stampbot.github_client.Auth.AppAuth"),
            patch("stampbot.github_client.GithubIntegration") as mock_integration_cls,
            patch("stampbot.github_client.Github") as mock_github_cls,
            patch("stampbot.github_client.create_span") as mock_span,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY
            mock_settings.otel_enabled = False

            mock_integration = Mock()
            mock_token = Mock()
            mock_token.token = TEST_TOKEN
            mock_integration.get_access_token.return_value = mock_token
            mock_integration_cls.return_value = mock_integration

            mock_repo = Mock()
            mock_repo.get_issue.side_effect = Exception("API Error")
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github_cls.return_value = mock_github

            mock_span.return_value.__enter__ = Mock(return_value=None)
            mock_span.return_value.__exit__ = Mock(return_value=False)

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            result = client.create_issue_comment(
                123456,
                "owner/repo",
                42,
                "Help text",
            )

            assert result is False


class TestSanitizeError:
    """Tests for _sanitize_error function."""

    def test_sanitize_installation_token(self):
        """Test that installation tokens (ghs_) are redacted."""
        from stampbot.github_client import _sanitize_error

        # Use obviously fake token (36 chars after prefix)
        fake_token = "ghs_" + "x" * 36  # pragma: allowlist secret
        error = Exception(f"Failed with token {fake_token}")
        result = _sanitize_error(error)
        assert "ghs_" not in result
        assert "[REDACTED]" in result
        assert "Failed with token" in result

    def test_sanitize_personal_access_token(self):
        """Test that personal access tokens (ghp_) are redacted."""
        from stampbot.github_client import _sanitize_error

        # Use obviously fake token (36 chars after prefix)
        fake_token = "ghp_" + "y" * 36  # pragma: allowlist secret
        error = Exception(f"Auth failed: {fake_token}")
        result = _sanitize_error(error)
        assert "ghp_" not in result
        assert "[REDACTED]" in result

    def test_sanitize_fine_grained_pat(self):
        """Test that fine-grained PATs (github_pat_) are redacted."""
        from stampbot.github_client import _sanitize_error

        error = Exception("Token: github_pat_11ABC123_abcdefghijklmnop")
        result = _sanitize_error(error)
        assert "github_pat_" not in result
        assert "[REDACTED]" in result

    def test_sanitize_preserves_non_token_content(self):
        """Test that non-token error messages are preserved."""
        from stampbot.github_client import _sanitize_error

        error = Exception("Connection timeout after 30 seconds")
        result = _sanitize_error(error)
        assert result == "Connection timeout after 30 seconds"

    def test_sanitize_multiple_tokens(self):
        """Test that multiple tokens in one message are all redacted."""
        from stampbot.github_client import _sanitize_error

        token1 = "ghs_token1token1token1token1token1token1"  # pragma: allowlist secret
        token2 = "ghp_token2token2token2token2token2token2"  # pragma: allowlist secret
        error = Exception(f"{token1} and {token2}")
        result = _sanitize_error(error)
        assert "ghs_" not in result
        assert "ghp_" not in result
        assert result.count("[REDACTED]") == 2
