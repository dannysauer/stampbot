"""Tests for configuration module."""

from unittest.mock import MagicMock, patch

import pytest

from stampbot.config import RepoConfig, get_setting, is_configured


def test_repo_config_from_toml():
    """Test parsing repository config from TOML."""
    toml_content = """
approval_labels = ["test", "autoapprove"]
auto_approve_on_label = true
chatops_enabled = true
chatops_required_permission = "write"
approve_commands = ["approve", "stamp"]
unapprove_commands = ["unapprove"]
"""
    config = RepoConfig.from_toml(toml_content)
    assert "test" in config.approval_labels
    assert "autoapprove" in config.approval_labels
    assert config.auto_approve_on_label is True
    assert config.chatops_enabled is True
    assert config.chatops_required_permission == "write"
    assert "approve" in config.approve_commands
    assert "unapprove" in config.unapprove_commands


def test_repo_config_default():
    """Test default repository config."""
    config = RepoConfig.default()
    assert isinstance(config.approval_labels, list)
    assert config.auto_approve_on_label is True
    assert config.chatops_enabled is True
    assert config.chatops_required_permission == "maintain"
    assert len(config.approve_commands) > 0
    assert len(config.unapprove_commands) > 0


def test_get_setting_returns_value():
    """Test get_setting returns setting value."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = "test_value"

        result = get_setting("test_key", "default")

        mock_settings.get.assert_called_once_with("test_key", "default")
        assert result == "test_value"


def test_get_setting_returns_default():
    """Test get_setting returns default when key not found."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = None

        result = get_setting("missing_key", "fallback")

        assert result is None


def test_is_configured_returns_true_when_all_set():
    """Test is_configured returns True when all credentials are set."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.side_effect = lambda key: {
            "app_id": "123",
            "private_key": "key",
            "webhook_secret": "secret",
        }.get(key)

        assert is_configured() is True


def test_is_configured_returns_false_when_missing():
    """Test is_configured returns False when credentials missing."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.side_effect = lambda key: {
            "app_id": "123",
            "private_key": None,
            "webhook_secret": "secret",
        }.get(key)

        assert is_configured() is False


def test_repo_config_from_toml_empty_content():
    """Test parsing empty TOML content uses defaults."""
    config = RepoConfig.from_toml("")
    # Should use default values
    assert "autoapprove" in config.approval_labels
    assert "stamp" in config.approval_labels
    assert config.auto_approve_on_label is True
    assert config.chatops_enabled is True
    assert config.chatops_required_permission == "maintain"


def test_repo_config_invalid_permission():
    """Test invalid permission raises a ValueError."""
    toml_content = 'chatops_required_permission = "invalid"'
    with pytest.raises(ValueError, match="Invalid chatops_required_permission"):
        RepoConfig.from_toml(toml_content)


def test_repo_config_get_defaults_with_settings_override():
    """Test _get_defaults respects settings.toml overrides."""
    with patch("stampbot.config.settings") as mock_settings:
        # Create a mock defaults object with only specific attributes
        # Using spec=[] means hasattr will return False for most attributes
        mock_defaults = MagicMock(
            spec=["approval_labels", "chatops_enabled", "chatops_required_permission"]
        )
        mock_defaults.approval_labels = ["custom-label"]
        mock_defaults.chatops_enabled = False
        mock_defaults.chatops_required_permission = "write"

        mock_settings.get.return_value = mock_defaults
        mock_settings.defaults = mock_defaults

        defaults = RepoConfig._get_defaults()

        # Should have the overridden values
        assert defaults["approval_labels"] == ["custom-label"]
        assert defaults["chatops_enabled"] is False
        assert defaults["chatops_required_permission"] == "write"
        # Non-overridden values should use REPO_CONFIG_DEFAULTS
        assert defaults["auto_approve_on_label"] is True
        assert defaults["approve_commands"] == ["approve", "stamp"]
        assert defaults["unapprove_commands"] == ["unapprove", "unstamp"]


def test_repo_config_get_defaults_without_settings_override():
    """Test _get_defaults uses REPO_CONFIG_DEFAULTS when no settings override."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = None

        defaults = RepoConfig._get_defaults()

        # Should use REPO_CONFIG_DEFAULTS
        assert "autoapprove" in defaults["approval_labels"]
        assert "stamp" in defaults["approval_labels"]


def test_repo_config_get_defaults_partial_override():
    """Test _get_defaults with partial settings override."""
    with patch("stampbot.config.settings") as mock_settings:
        # Create a mock defaults object with only some attributes
        mock_defaults = MagicMock(spec=[])  # Empty spec means no attributes
        # But we can still check hasattr behavior by setting one attribute
        mock_defaults.approval_labels = ["override-only-this"]

        mock_settings.get.return_value = mock_defaults
        mock_settings.defaults = mock_defaults

        defaults = RepoConfig._get_defaults()

        # The overridden value
        assert defaults["approval_labels"] == ["override-only-this"]
        # Non-overridden values should use REPO_CONFIG_DEFAULTS
        assert defaults["auto_approve_on_label"] is True
        assert defaults["chatops_enabled"] is True
        assert defaults["chatops_required_permission"] == "maintain"


def test_repo_config_with_required_labels():
    """Test parsing repository config with required labels."""
    toml_content = """
required_labels = ["dependencies", "automated"]
"""
    config = RepoConfig.from_toml(toml_content)
    assert config.required_labels == ["dependencies", "automated"]


def test_repo_config_with_required_title_patterns():
    """Test parsing repository config with required title patterns."""
    toml_content = """
required_title_patterns = ["^\\\\[bot\\\\]", "^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    assert len(config.required_title_patterns) == 2
    assert "^\\[bot\\]" in config.required_title_patterns
    assert "^chore:" in config.required_title_patterns


def test_repo_config_invalid_regex_pattern():
    """Test invalid regex pattern raises a ValueError."""
    toml_content = """
required_title_patterns = ["[invalid"]
"""
    with pytest.raises(ValueError, match="Invalid regex pattern"):
        RepoConfig.from_toml(toml_content)


def test_is_pr_eligible_no_filters():
    """Test is_pr_eligible returns True when no filters configured."""
    config = RepoConfig.default()
    is_eligible, reason = config.is_pr_eligible(["some-label"], "Some PR title")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_required_label_present():
    """Test is_pr_eligible returns True when required label is present."""
    config = RepoConfig.from_toml('required_labels = ["dependencies", "automated"]')
    is_eligible, reason = config.is_pr_eligible(["dependencies"], "PR title")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_required_label_missing():
    """Test is_pr_eligible returns False when required label is missing."""
    config = RepoConfig.from_toml('required_labels = ["dependencies", "automated"]')
    is_eligible, reason = config.is_pr_eligible(["other-label"], "PR title")
    assert is_eligible is False
    assert "missing required label" in reason


def test_is_pr_eligible_title_pattern_matches():
    """Test is_pr_eligible returns True when title matches pattern."""
    config = RepoConfig.from_toml('required_title_patterns = ["^chore:", "^\\\\[bot\\\\]"]')
    is_eligible, reason = config.is_pr_eligible([], "chore: update dependencies")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_title_pattern_no_match():
    """Test is_pr_eligible returns False when title doesn't match any pattern."""
    config = RepoConfig.from_toml('required_title_patterns = ["^chore:", "^\\\\[bot\\\\]"]')
    is_eligible, reason = config.is_pr_eligible([], "feat: add new feature")
    assert is_eligible is False
    assert "does not match any required pattern" in reason


def test_is_pr_eligible_both_filters_pass():
    """Test is_pr_eligible returns True when both filters pass."""
    toml_content = """
required_labels = ["automated"]
required_title_patterns = ["^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(["automated"], "chore: update deps")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_label_fails_title_passes():
    """Test is_pr_eligible returns False when label filter fails."""
    toml_content = """
required_labels = ["automated"]
required_title_patterns = ["^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(["other"], "chore: update deps")
    assert is_eligible is False
    assert "missing required label" in reason


def test_is_pr_eligible_label_passes_title_fails():
    """Test is_pr_eligible returns False when title filter fails."""
    toml_content = """
required_labels = ["automated"]
required_title_patterns = ["^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(["automated"], "feat: new feature")
    assert is_eligible is False
    assert "does not match any required pattern" in reason
