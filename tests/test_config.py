"""Tests for configuration module."""

from unittest.mock import MagicMock, patch

import pytest
import regex

from stampbot.config import (
    MAX_PR_TITLE_LENGTH,
    MAX_TITLE_PATTERN_COUNT,
    MAX_TITLE_PATTERN_LENGTH,
    TITLE_PATTERN_TIMEOUT_SECONDS,
    RepoConfig,
    get_setting,
    is_configured,
)


def test_repo_config_from_toml():
    """Test parsing repository config from TOML."""
    toml_content = """
approval_labels = ["test", "autoapprove"]
auto_approve_on_label = true
reapprove = true
chatops_enabled = true
chatops_required_permission = "write"
approve_commands = ["approve", "stamp"]
unapprove_commands = ["unapprove"]
"""
    config = RepoConfig.from_toml(toml_content)
    assert "test" in config.approval_labels
    assert "autoapprove" in config.approval_labels
    assert config.auto_approve_on_label is True
    assert config.reapprove is True
    assert config.chatops_enabled is True
    assert config.chatops_required_permission == "write"
    assert "approve" in config.approve_commands
    assert "unapprove" in config.unapprove_commands


def test_repo_config_default():
    """Test default repository config."""
    config = RepoConfig.default()
    assert isinstance(config.approval_labels, list)
    assert config.auto_approve_on_label is True
    assert config.reapprove is False
    assert config.chatops_enabled is True
    assert config.chatops_required_permission == "maintain"
    assert len(config.approve_commands) > 0
    assert len(config.unapprove_commands) > 0


def test_repo_config_fail_closed_does_not_read_service_defaults():
    """Test fail-closed config is independent of invalid service settings."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.side_effect = AssertionError("service settings must not be read")

        config = RepoConfig.fail_closed("invalid service configuration")

    assert config.config_error == "invalid service configuration"
    assert config.approve_commands == ["approve", "stamp"]
    assert config.unapprove_commands == ["unapprove", "unstamp"]


def test_repo_config_fail_closed_requires_no_trusted_error_input():
    """Test even an empty error cannot accidentally enable the fallback."""
    config = RepoConfig.fail_closed("")

    assert config.config_error == "Invalid configuration"


def test_repo_config_default_or_config_error_preserves_valid_defaults():
    """Test valid configured defaults remain active."""
    config = RepoConfig.default_or_config_error()

    assert config.config_error is None
    assert config.approve_commands == ["approve", "stamp"]


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("approve_commands", "approve", "must be a list of strings"),
        ("unapprove_commands", ["unapprove", 1], "must contain only strings"),
    ],
)
def test_repo_config_default_or_config_error_fails_closed(setting, value, message):
    """Test invalid command defaults produce a non-authorizing config."""
    service_defaults = MagicMock(spec=[setting])
    setattr(service_defaults, setting, value)

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = service_defaults
        mock_settings.defaults = service_defaults

        config = RepoConfig.default_or_config_error()

    assert config.config_error is not None
    assert "Invalid service default configuration" in config.config_error
    assert message in config.config_error
    assert config.approve_commands == ["approve", "stamp"]
    assert config.unapprove_commands == ["unapprove", "unstamp"]


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
    assert config.reapprove is False
    assert config.chatops_enabled is True
    assert config.chatops_required_permission == "maintain"


def test_repo_config_from_toml_parser_error():
    """Test parser errors are normalized as config validation errors."""
    with pytest.raises(ValueError, match="Invalid TOML content"):
        RepoConfig.from_toml(";\r")


def test_repo_config_invalid_permission():
    """Test invalid permission raises a ValueError."""
    toml_content = 'chatops_required_permission = "invalid"'
    with pytest.raises(ValueError, match="Invalid chatops_required_permission"):
        RepoConfig.from_toml(toml_content)


@pytest.mark.parametrize("setting", ["approve_commands", "unapprove_commands"])
@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('"approve"', "must be a list of strings"),
        ("[1]", "must contain only strings"),
    ],
)
def test_repo_config_rejects_invalid_command_lists(setting, value, message):
    """Test ChatOps command settings fail closed unless they are string lists."""
    with pytest.raises(ValueError, match=message):
        RepoConfig.from_toml(f"{setting} = {value}")


def test_repo_config_detaches_valid_command_lists():
    """Test a caller cannot mutate command settings through its input lists."""
    approve_commands = ["approve"]
    unapprove_commands = ["unapprove"]

    config = RepoConfig(
        approval_labels=[],
        auto_approve_on_label=True,
        reapprove=False,
        chatops_enabled=True,
        chatops_required_permission="maintain",
        approve_commands=approve_commands,
        unapprove_commands=unapprove_commands,
    )
    approve_commands.append("stamp")
    unapprove_commands.append("unstamp")

    assert config.approve_commands == ["approve"]
    assert config.unapprove_commands == ["unapprove"]


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


@pytest.mark.parametrize(
    ("toml_content", "message"),
    [
        ('required_title_patterns = "^fix:"', "must be a list of strings"),
        ("required_title_patterns = [1]", "must contain only strings"),
    ],
)
def test_repo_config_rejects_invalid_title_pattern_types(toml_content, message):
    """Test title pattern configuration requires a list of strings."""
    with pytest.raises(ValueError, match=message):
        RepoConfig.from_toml(toml_content)


def test_repo_config_rejects_too_many_title_patterns():
    """Test title pattern count is bounded."""
    patterns = ", ".join('"^fix:"' for _ in range(MAX_TITLE_PATTERN_COUNT + 1))

    with pytest.raises(ValueError, match="too many patterns"):
        RepoConfig.from_toml(f"required_title_patterns = [{patterns}]")


def test_repo_config_rejects_long_title_pattern():
    """Test individual title pattern length is bounded."""
    pattern = "a" * (MAX_TITLE_PATTERN_LENGTH + 1)

    with pytest.raises(ValueError, match="pattern longer than"):
        RepoConfig.from_toml(f'required_title_patterns = ["{pattern}"]')


def test_repo_config_accepts_title_pattern_boundaries():
    """Test pattern count and length limits are inclusive."""
    patterns = ["a" * MAX_TITLE_PATTERN_LENGTH]
    patterns.extend("^fix:" for _ in range(MAX_TITLE_PATTERN_COUNT - 1))

    config = RepoConfig.from_toml(f"required_title_patterns = {patterns!r}")

    assert len(config.required_title_patterns) == MAX_TITLE_PATTERN_COUNT


def test_is_pr_eligible_no_filters():
    """Test is_pr_eligible returns True when no filters configured."""
    config = RepoConfig.default()
    is_eligible, reason = config.is_pr_eligible(["some-label"], "Some PR title", "someuser")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_required_label_present():
    """Test is_pr_eligible returns True when required label is present."""
    config = RepoConfig.from_toml('required_labels = ["dependencies", "automated"]')
    is_eligible, reason = config.is_pr_eligible(["dependencies"], "PR title", "someuser")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_required_label_missing():
    """Test is_pr_eligible returns False when required label is missing."""
    config = RepoConfig.from_toml('required_labels = ["dependencies", "automated"]')
    is_eligible, reason = config.is_pr_eligible(["other-label"], "PR title", "someuser")
    assert is_eligible is False
    assert "missing required label" in reason


def test_is_pr_eligible_required_label_second_matches():
    """Test is_pr_eligible returns True when second of multiple required labels matches."""
    config = RepoConfig.from_toml('required_labels = ["dependencies", "automated", "bot"]')
    # PR has "automated" which is the second label in the list
    is_eligible, reason = config.is_pr_eligible(["automated"], "PR title", "someuser")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_title_pattern_matches():
    """Test is_pr_eligible returns True when title matches pattern."""
    config = RepoConfig.from_toml('required_title_patterns = ["^chore:", "^\\\\[bot\\\\]"]')
    is_eligible, reason = config.is_pr_eligible([], "chore: update dependencies", "someuser")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_title_pattern_no_match():
    """Test is_pr_eligible returns False when title doesn't match any pattern."""
    config = RepoConfig.from_toml('required_title_patterns = ["^chore:", "^\\\\[bot\\\\]"]')
    is_eligible, reason = config.is_pr_eligible([], "feat: add new feature", "someuser")
    assert is_eligible is False
    assert "does not match any required pattern" in reason


def test_is_pr_eligible_title_pattern_second_matches():
    """Test is_pr_eligible returns True when second of multiple patterns matches."""
    config = RepoConfig.from_toml(
        'required_title_patterns = ["^chore:", "^\\\\[bot\\\\]", "^fix:"]'
    )
    # Title matches the second pattern
    is_eligible, reason = config.is_pr_eligible([], "[bot] Update deps", "someuser")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_preserves_scoped_inline_flag_semantics():
    """Test a scoped flag cannot make the rest of a title pattern case-insensitive."""
    config = RepoConfig.from_toml('required_title_patterns = ["(?i:fix): [A-Z]+"]')

    is_eligible, reason = config.is_pr_eligible([], "FIX: lowercase", "someuser")

    assert is_eligible is False
    assert reason == "PR title does not match any required pattern"


def test_is_pr_eligible_rejects_overlong_title_before_matching():
    """Test webhook title input is bounded before regex evaluation."""
    config = RepoConfig.from_toml('required_title_patterns = [".*"]')
    config._compiled_title_patterns[0] = MagicMock()

    is_eligible, reason = config.is_pr_eligible([], "a" * (MAX_PR_TITLE_LENGTH + 1), "someuser")

    assert is_eligible is False
    assert f"{MAX_PR_TITLE_LENGTH}-character safety limit" in reason
    config._compiled_title_patterns[0].search.assert_not_called()


def test_is_pr_eligible_rejects_non_text_title():
    """Test malformed webhook title input fails closed."""
    config = RepoConfig.from_toml('required_title_patterns = [".*"]')

    is_eligible, reason = config.is_pr_eligible([], None, "someuser")  # type: ignore[arg-type]

    assert is_eligible is False
    assert reason == "PR title is not valid text"


def test_is_pr_eligible_pathological_title_pattern_times_out():
    """Test catastrophic backtracking is stopped by the match timeout."""
    config = RepoConfig.from_toml('required_title_patterns = ["(a|aa)+$"]')

    is_eligible, reason = config.is_pr_eligible(
        [], "a" * (MAX_PR_TITLE_LENGTH - 1) + "!", "someuser"
    )

    assert is_eligible is False
    assert f"{int(TITLE_PATTERN_TIMEOUT_SECONDS * 1000)} ms safety limit" in reason


def test_is_pr_eligible_regex_engine_failure_is_closed():
    """Test a regex engine failure cannot approve a pull request."""
    config = RepoConfig.from_toml('required_title_patterns = ["^fix:"]')
    config._compiled_title_patterns[0] = MagicMock()
    config._compiled_title_patterns[0].search.side_effect = regex.error("engine failure")

    is_eligible, reason = config.is_pr_eligible([], "fix: dependency", "someuser")

    assert is_eligible is False
    assert reason == "PR title pattern evaluation failed safely"


def test_is_pr_eligible_both_filters_pass():
    """Test is_pr_eligible returns True when both filters pass."""
    toml_content = """
required_labels = ["automated"]
required_title_patterns = ["^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(["automated"], "chore: update deps", "someuser")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_label_fails_title_passes():
    """Test is_pr_eligible returns False when label filter fails."""
    toml_content = """
required_labels = ["automated"]
required_title_patterns = ["^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(["other"], "chore: update deps", "someuser")
    assert is_eligible is False
    assert "missing required label" in reason


def test_is_pr_eligible_label_passes_title_fails():
    """Test is_pr_eligible returns False when title filter fails."""
    toml_content = """
required_labels = ["automated"]
required_title_patterns = ["^chore:"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(["automated"], "feat: new feature", "someuser")
    assert is_eligible is False
    assert "does not match any required pattern" in reason


def test_is_pr_eligible_allowed_user():
    """Test is_pr_eligible returns True when author is in allowed_users."""
    config = RepoConfig.from_toml('allowed_users = ["dependabot[bot]", "renovate[bot]"]')
    is_eligible, reason = config.is_pr_eligible([], "Update deps", "dependabot[bot]")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_user_not_allowed():
    """Test is_pr_eligible returns False when author is not in allowed_users."""
    config = RepoConfig.from_toml('allowed_users = ["dependabot[bot]", "renovate[bot]"]')
    is_eligible, reason = config.is_pr_eligible([], "Update deps", "random-user")
    assert is_eligible is False
    assert "not in allowed users" in reason


def test_is_pr_eligible_allowed_team():
    """Test is_pr_eligible returns True when author is in an allowed team."""
    config = RepoConfig.from_toml('allowed_teams = ["my-org/release-team"]')
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "team-member", author_team_slugs=["release-team"]
    )
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_team_not_matched():
    """Test is_pr_eligible returns False when author is not in any allowed team."""
    config = RepoConfig.from_toml('allowed_teams = ["my-org/release-team"]')
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "non-member", author_team_slugs=["other-team"]
    )
    assert is_eligible is False
    assert "not a member of any allowed team" in reason


def test_is_pr_eligible_user_or_team():
    """Test is_pr_eligible returns True when user matches even if not in team."""
    toml_content = """
allowed_users = ["special-user"]
allowed_teams = ["my-org/release-team"]
"""
    config = RepoConfig.from_toml(toml_content)
    # User is in allowed_users, so no team check needed
    is_eligible, reason = config.is_pr_eligible([], "Update deps", "special-user")
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_neither_user_nor_team():
    """Test is_pr_eligible returns False when neither user nor team matches."""
    toml_content = """
allowed_users = ["special-user"]
allowed_teams = ["my-org/release-team"]
"""
    config = RepoConfig.from_toml(toml_content)
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "random-user", author_team_slugs=["other-team"]
    )
    assert is_eligible is False
    assert "not in allowed users or teams" in reason


def test_is_pr_eligible_not_in_users_but_in_team():
    """Test is_pr_eligible returns True when user not in allowed_users but is in allowed_team."""
    toml_content = """
allowed_users = ["special-user"]
allowed_teams = ["my-org/release-team"]
"""
    config = RepoConfig.from_toml(toml_content)
    # User is NOT in allowed_users but IS in allowed_teams
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "team-member", author_team_slugs=["release-team"]
    )
    assert is_eligible is True
    assert reason is None


def test_is_pr_eligible_multiple_teams_second_matches():
    """Test is_pr_eligible returns True when user is in second of multiple allowed teams."""
    config = RepoConfig.from_toml(
        'allowed_teams = ["my-org/admin-team", "my-org/release-team", "my-org/deploy-team"]'
    )
    # User is only in release-team (the second one)
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "team-member", author_team_slugs=["release-team"]
    )
    assert is_eligible is True
    assert reason is None


def test_needs_team_check_no_teams_configured():
    """Test needs_team_check returns False when no teams configured."""
    config = RepoConfig.from_toml('allowed_users = ["some-user"]')
    assert config.needs_team_check("any-user") is False


def test_needs_team_check_user_in_allowed_users():
    """Test needs_team_check returns False when user is in allowed_users."""
    toml_content = """
allowed_users = ["special-user"]
allowed_teams = ["my-org/release-team"]
"""
    config = RepoConfig.from_toml(toml_content)
    assert config.needs_team_check("special-user") is False


def test_needs_team_check_user_not_in_allowed_users():
    """Test needs_team_check returns True when user not in allowed_users but teams configured."""
    toml_content = """
allowed_users = ["special-user"]
allowed_teams = ["my-org/release-team"]
"""
    config = RepoConfig.from_toml(toml_content)
    assert config.needs_team_check("other-user") is True
