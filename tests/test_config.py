"""Tests for configuration module."""

import re
from unittest.mock import MagicMock, patch

import pytest
import regex

from stampbot.config import (
    DEFAULTS_INVALID,
    MAX_PR_TITLE_LENGTH,
    MAX_TITLE_PATTERN_COUNT,
    MAX_TITLE_PATTERN_LENGTH,
    REPO_CONFIG_DEFAULTS,
    SERVICE_DEFAULTS_SOURCE,
    TITLE_PATTERN_TIMEOUT_SECONDS,
    RepoConfig,
    get_setting,
    is_configured,
    repo_config_cache_seconds,
)
from stampbot.principals import MAX_PRINCIPAL_LENGTH, MAX_PRINCIPALS


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
        (
            "chatops_required_permission",
            "owner",
            "Invalid chatops_required_permission",
        ),
        (
            "chatops_required_permission",
            ["maintain"],
            "Invalid chatops_required_permission",
        ),
    ],
)
def test_repo_config_default_or_config_error_fails_closed(setting, value, message):
    """Test invalid service defaults produce a non-authorizing config."""
    service_defaults = MagicMock(spec=[setting])
    setattr(service_defaults, setting, value)

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = service_defaults
        mock_settings.defaults = service_defaults

        config = RepoConfig.default_or_config_error()

    assert config.config_error == DEFAULTS_INVALID
    assert "Invalid service default configuration" in config.config_error_detail
    assert message in config.config_error_detail
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


def test_is_pr_eligible_allowed_author_login():
    """Test a listed login passes the author filter, with or without a leading @."""
    config = RepoConfig.from_toml('allowed_authors = ["dependabot[bot]", "@renovate[bot]"]')
    assert config.is_pr_eligible([], "Update deps", "dependabot[bot]") == (True, None)
    assert config.is_pr_eligible([], "Update deps", "renovate[bot]") == (True, None)


def test_is_pr_eligible_allowed_author_login_ignores_case():
    """Test logins compare case-insensitively, as GitHub does."""
    config = RepoConfig.from_toml('allowed_authors = ["Renovate[bot]"]')
    assert config.is_pr_eligible([], "Update deps", "renovate[BOT]") == (True, None)


def test_is_pr_eligible_author_not_allowed():
    """Test an unlisted author fails the author filter."""
    config = RepoConfig.from_toml('allowed_authors = ["dependabot[bot]", "renovate[bot]"]')
    is_eligible, reason = config.is_pr_eligible([], "Update deps", "random-user")
    assert is_eligible is False
    assert reason == "PR author is not in allowed_authors"


def test_is_pr_eligible_allowed_author_team():
    """Test membership in a listed team passes the author filter."""
    config = RepoConfig.from_toml('allowed_authors = ["@my-org/Release-Team"]')
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "team-member", author_team_slugs=["release-team"]
    )
    assert (is_eligible, reason) == (True, None)


def test_is_pr_eligible_team_not_matched():
    """Test membership in an unlisted team fails the author filter."""
    config = RepoConfig.from_toml('allowed_authors = ["@my-org/release-team"]')
    is_eligible, reason = config.is_pr_eligible(
        [], "Update deps", "non-member", author_team_slugs=["other-team"]
    )
    assert is_eligible is False
    assert reason == "PR author is not in allowed_authors"


def test_is_pr_eligible_login_or_team():
    """Test logins and teams in one list are alternatives."""
    config = RepoConfig.from_toml(
        'allowed_authors = ["renovate[bot]", "@my-org/admin-team", "@my-org/release-team"]'
    )
    assert config.is_pr_eligible([], "Update deps", "renovate[bot]") == (True, None)
    assert config.is_pr_eligible(
        [], "Update deps", "team-member", author_team_slugs=["release-team"]
    ) == (True, None)
    assert (
        config.is_pr_eligible([], "Update deps", "random-user", author_team_slugs=["other-team"])[0]
        is False
    )


def test_needs_team_check():
    """Test team lookup happens only when teams are listed and the login isn't."""
    assert RepoConfig.from_toml('allowed_authors = ["some-user"]').needs_team_check("x") is False
    config = RepoConfig.from_toml('allowed_authors = ["special-user", "@my-org/release-team"]')
    assert config.needs_team_check("Special-User") is False
    assert config.needs_team_check("other-user") is True


def test_deprecated_author_settings_merge_into_allowed_authors():
    """Test allowed_users and allowed_teams still work and are reported as deprecated."""
    config = RepoConfig.from_toml(
        """
allowed_authors = ["@my-org/deploy-team"]
allowed_users = ["special-user"]
allowed_teams = ["my-org/release-team", "admin-team"]
"""
    )
    assert config.allowed_authors.describe() == [
        "@special-user",
        "@my-org/deploy-team",
        "@my-org/release-team",
        "admin-team (team)",
    ]
    assert config.deprecated_settings == ("allowed_users", "allowed_teams")
    assert config.allowed_authors.team_slugs() == ["deploy-team", "release-team", "admin-team"]


@pytest.mark.parametrize("key", ["allowed_users", "allowed_teams"])
def test_empty_deprecated_key_is_reported(key):
    """Test an empty deprecated key counts as use, since it replaces the default filter."""
    config = RepoConfig.from_toml(f"{key} = []")
    assert config.deprecated_settings == (key,)


def test_allowed_authors_reports_no_deprecated_settings():
    """Test the new setting alone reports nothing deprecated."""
    config = RepoConfig.from_toml('allowed_authors = ["renovate[bot]"]')
    assert config.deprecated_settings == ()


def test_repo_author_filter_replaces_service_default_author_filter():
    """Test a policy that sets any author key drops every author key from defaults."""
    defaults = {
        **REPO_CONFIG_DEFAULTS,
        "allowed_teams": ["default-team"],
    }
    with patch.object(RepoConfig, "_get_defaults", return_value=defaults):
        config = RepoConfig.from_toml('allowed_authors = ["renovate[bot]"]')
        inherited = RepoConfig.from_toml('approval_labels = ["ship-it"]')

    assert config.allowed_authors.describe() == ["@renovate[bot]"]
    assert inherited.allowed_authors.describe("acme") == ["@acme/default-team"]
    assert config.author_filter_from_defaults is False
    assert inherited.author_filter_from_defaults is True


@pytest.mark.parametrize(
    ("toml_content", "message"),
    [
        ('allowed_authors = "renovate[bot]"', "allowed_authors must be a list of strings"),
        ("allowed_authors = [1]", "allowed_authors must contain only strings"),
        ('allowed_authors = ["has space"]', "is not a valid GitHub login"),
        ('allowed_authors = [""]', "is not a valid GitHub login"),
        ('allowed_authors = ["@"]', "is not a valid GitHub login"),
        ('allowed_authors = ["@org/"]', "is not a valid team"),
        ('allowed_authors = ["@a/b/c"]', "is not a valid team"),
        ('allowed_users = ["my-org/team"]', "list teams in allowed_authors instead"),
        ('allowed_teams = ["bad team"]', "is not a valid team"),
        (f'allowed_authors = ["{"a" * (MAX_PRINCIPAL_LENGTH + 1)}"]', "longer than"),
        (
            "allowed_authors = [" + ", ".join(f'"u{i}"' for i in range(MAX_PRINCIPALS + 1)) + "]",
            "too many entries",
        ),
        (
            "allowed_authors = ["
            + ", ".join(f'"u{i}"' for i in range(MAX_PRINCIPALS))
            + ']\nallowed_users = ["extra"]',
            "too many entries together",
        ),
    ],
)
def test_invalid_author_filter_is_rejected(toml_content, message):
    """Test malformed author entries fail policy validation."""
    with pytest.raises(ValueError, match=re.escape(message)):
        RepoConfig.from_toml(toml_content)


def test_check_team_owner_accepts_owner_org_and_bare_slugs():
    """Test teams in the owner's organization, in any case, pass the owner check."""
    config = RepoConfig.from_toml(
        """
allowed_authors = ["@My-Org/release-team", "renovate[bot]"]
allowed_teams = ["admin-team"]
"""
    )
    config.check_team_owner("my-org")


def test_check_team_owner_rejects_other_org():
    """Test a team outside the owner's organization fails with a fix to apply."""
    config = RepoConfig.from_toml('allowed_authors = ["@my-org/release-team"]')
    config.author_filter_source = "stampbot.toml in other-org/repo"
    with pytest.raises(ValueError) as error:
        config.check_team_owner("other-org")

    assert str(error.value) == (
        "The allowed_authors entry '@my-org/release-team' in stampbot.toml in other-org/repo "
        "names the 'my-org' organization, but this repository belongs to 'other-org'. "
        "Stampbot only looks up teams in the repository owner's organization. "
        "Change the entry to '@other-org/release-team' or remove it."
    )


def test_check_team_owner_rejects_unknown_owner():
    """Test a team entry fails closed when GitHub didn't name the owner."""
    config = RepoConfig.from_toml('allowed_authors = ["@my-org/release-team"]')
    with pytest.raises(ValueError, match="GitHub's event didn't say who owns this repository"):
        config.check_team_owner(None)


def test_check_team_owner_names_deprecated_setting():
    """Test the owner error names the setting the team came from."""
    config = RepoConfig.from_toml('allowed_teams = ["my-org/release-team"]')
    with pytest.raises(ValueError, match=r"^The allowed_teams entry '@my-org/release-team' in"):
        config.check_team_owner("other-org")


def test_check_team_owner_blames_service_defaults():
    """Test a bad team inherited from defaults names the defaults, not the policy file."""
    defaults = {**REPO_CONFIG_DEFAULTS, "allowed_authors": ["@my-org/release-team"]}
    with patch.object(RepoConfig, "_get_defaults", return_value=defaults):
        inherited = RepoConfig.from_toml('approval_labels = ["ship-it"]')
        default = RepoConfig.default()

    for config in (inherited, default):
        with pytest.raises(ValueError) as error:
            config.check_team_owner("other-org")
        assert str(error.value).startswith(f"A team in {SERVICE_DEFAULTS_SOURCE} belongs to")
        assert "release-team" not in str(error.value)


def test_invalid_legacy_service_default_fails_closed():
    """Test an invalid allowed_users default stops automation instead of matching."""
    defaults = {**REPO_CONFIG_DEFAULTS, "allowed_users": ["has space"]}
    with patch.object(RepoConfig, "_get_defaults", return_value=defaults):
        config = RepoConfig.default_or_config_error()

    assert config.config_error == DEFAULTS_INVALID
    assert (
        "allowed_users entry 'has space' is not a valid GitHub login" in config.config_error_detail
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(300, 300), ("120", 120), (0, 0), (5.0, 5)],
)
def test_repo_config_cache_seconds_accepts_integers(raw, expected):
    """Test the cache lifetime accepts integer-valued settings."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = raw

        assert repo_config_cache_seconds() == expected


def test_repo_config_cache_seconds_defaults():
    """Test the cache lifetime defaults to five minutes."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.side_effect = lambda _key, default=None: default

        assert repo_config_cache_seconds() == 300


@pytest.mark.parametrize("raw", ["soon", None, -5, "-1", [300], True, False, 1.9, "1.9"])
def test_repo_config_cache_seconds_rejects_invalid_values(raw):
    """Test a non-integer or negative lifetime fails at startup instead of guessing."""
    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = raw

        with pytest.raises(ValueError, match="repo_config_cache_seconds"):
            repo_config_cache_seconds()
