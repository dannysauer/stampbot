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
        """Test that _load_private_key raises when file cannot be read."""
        with patch("stampbot.github_client.settings") as mock_settings:
            mock_settings.private_key = "/nonexistent/path/to/key.pem"

            from stampbot.github_client import GitHubAppClient

            client = GitHubAppClient()
            with pytest.raises(FileNotFoundError):
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

            mock_bot_user = Mock()
            mock_bot_user.login = "stampbot[bot]"

            mock_pr = Mock()
            mock_pr.get_reviews.return_value = [bot_review, other_review]
            mock_repo = Mock()
            mock_repo.get_pull.return_value = mock_pr
            mock_github = Mock()
            mock_github.get_repo.return_value = mock_repo
            mock_github.get_user.return_value = mock_bot_user
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
