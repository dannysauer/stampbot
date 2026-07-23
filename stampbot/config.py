# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Configuration management using dynaconf."""

from __future__ import annotations

from typing import Any

import regex  # type: ignore[import-untyped]
from dynaconf import Dynaconf  # type: ignore[import-untyped]

# GitHub currently limits pull request titles to 256 characters. Keep a local
# bound because webhook JSON is still untrusted input. Repository patterns are
# bounded separately so a policy cannot multiply matching work without limit.
MAX_PR_TITLE_LENGTH = 256
MAX_TITLE_PATTERN_COUNT = 20
MAX_TITLE_PATTERN_LENGTH = 256
TITLE_PATTERN_TIMEOUT_SECONDS = 0.01

# Default values for repo configuration
# These can be overridden in settings.toml or per-repo stampbot.toml
REPO_CONFIG_DEFAULTS = {
    "approval_labels": ["autoapprove", "stamp"],
    "auto_approve_on_label": True,
    "reapprove": False,
    "chatops_enabled": True,
    "chatops_required_permission": "maintain",
    "approve_commands": ["approve", "stamp"],
    "unapprove_commands": ["unapprove", "unstamp"],
    # Optional filters - if empty, no filtering is applied
    # PR must have at least one of these labels to be eligible for auto-approval
    "required_labels": [],
    # PR title must match at least one of these regex patterns to be eligible
    "required_title_patterns": [],
    # PR author must be one of these users (login names)
    "allowed_users": [],
    # PR author must be a member of one of these teams (org/team-slug or team-slug)
    "allowed_teams": [],
}
REPO_PERMISSION_LEVELS = ["none", "read", "triage", "write", "maintain", "admin"]

# App-level settings from environment variables and settings files
# Dynaconf automatically reads from:
# - Environment variables with STAMPBOT_ prefix
# - settings.toml, .secrets.toml in the project root
# - .env file
settings = Dynaconf(
    envvar_prefix="STAMPBOT",
    settings_files=["settings.toml", ".secrets.toml"],
    environments=False,  # We don't use [development]/[production] sections
    load_dotenv=True,
)


def is_configured() -> bool:
    """Check if all required GitHub App credentials are configured.

    Returns:
        True if app_id, private_key, and webhook_secret are all set.
    """
    return all(
        [
            settings.get("app_id"),
            settings.get("private_key"),
            settings.get("webhook_secret"),
        ]
    )


def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value with a default fallback.

    Args:
        key: Setting key (case-insensitive)
        default: Default value if not set

    Returns:
        Setting value or default
    """
    return settings.get(key, default)


class RepoConfig:
    """Repository-specific configuration, merged with app defaults.

    Configuration is loaded from the repo's stampbot.toml file and merged
    with the app-level defaults defined in REPO_CONFIG_DEFAULTS or settings.
    """

    def __init__(
        self,
        approval_labels: list[str],
        auto_approve_on_label: bool,
        reapprove: bool,
        chatops_enabled: bool,
        chatops_required_permission: str,
        approve_commands: list[str],
        unapprove_commands: list[str],
        required_labels: list[str] | None = None,
        required_title_patterns: list[str] | None = None,
        allowed_users: list[str] | None = None,
        allowed_teams: list[str] | None = None,
        config_error: str | None = None,
    ):
        """Initialize repo configuration.

        Args:
            approval_labels: Labels that trigger auto-approval
            auto_approve_on_label: Whether to auto-approve when label is added
            reapprove: Whether to re-approve when new commits are pushed
            chatops_enabled: Whether chatops commands are enabled
            chatops_required_permission: Minimum repo permission for chatops commands
            approve_commands: Commands that trigger approval
            unapprove_commands: Commands that dismiss approval
            required_labels: Labels required for auto-approval eligibility (any match)
            required_title_patterns: Regex patterns for PR title (any match)
            allowed_users: User logins allowed for auto-approval (any match)
            allowed_teams: Team slugs allowed for auto-approval (any match)
            config_error: Config error message if config was invalid
        """
        self.approval_labels = approval_labels
        self.auto_approve_on_label = auto_approve_on_label
        self.reapprove = reapprove
        self.chatops_enabled = chatops_enabled
        self.chatops_required_permission = chatops_required_permission
        self.approve_commands = self._validate_command_list(
            approve_commands,
            "approve_commands",
        )
        self.unapprove_commands = self._validate_command_list(
            unapprove_commands,
            "unapprove_commands",
        )
        self.required_labels = required_labels or []
        title_patterns = required_title_patterns if required_title_patterns is not None else []
        self._compiled_title_patterns = self._compile_title_patterns(title_patterns)
        self.required_title_patterns = list(title_patterns)
        self.allowed_users = allowed_users or []
        self.allowed_teams = allowed_teams or []
        self.config_error = config_error

    @staticmethod
    def _validate_command_list(commands: object, setting: str) -> list[str]:
        """Require one command setting to be a list containing only strings.

        Args:
            commands: Parsed application or repository setting.
            setting: Setting name used in the validation error.

        Returns:
            A detached copy of the validated command list.

        Raises:
            ValueError: If the setting is not a list of strings.
        """
        if not isinstance(commands, list):
            raise ValueError(f"{setting} must be a list of strings")
        if any(not isinstance(command, str) for command in commands):
            raise ValueError(f"{setting} must contain only strings")
        return list(commands)

    @staticmethod
    def _compile_title_patterns(patterns: list[str]) -> list[Any]:
        """Validate and compile bounded pull request title patterns.

        Args:
            patterns: Configured title patterns.

        Returns:
            Patterns compiled in Python ``re`` compatibility mode.

        Raises:
            ValueError: If the pattern list, an item, or a pattern is invalid.
        """
        if not isinstance(patterns, list):
            raise ValueError("required_title_patterns must be a list of strings")
        if len(patterns) > MAX_TITLE_PATTERN_COUNT:
            raise ValueError(
                "required_title_patterns contains too many patterns "
                f"(maximum: {MAX_TITLE_PATTERN_COUNT})"
            )

        compiled_patterns = []
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise ValueError("required_title_patterns must contain only strings")
            if len(pattern) > MAX_TITLE_PATTERN_LENGTH:
                raise ValueError(
                    "required_title_patterns contains a pattern longer than "
                    f"{MAX_TITLE_PATTERN_LENGTH} characters"
                )
            try:
                compiled_patterns.append(regex.compile(pattern, regex.VERSION0))
            except (regex.error, OverflowError) as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}") from e

        return compiled_patterns

    @classmethod
    def fail_closed(cls, message: str) -> RepoConfig:
        """Return a valid config object that cannot authorize automation.

        This fallback deliberately uses code-defined values rather than
        application settings. The attached config error causes webhook
        handlers to stop before evaluating any of the policy fields.

        Args:
            message: Configuration error that caused the fallback.

        Returns:
            Structurally valid built-in configuration with config_error set.
        """
        return cls(
            approval_labels=["autoapprove", "stamp"],
            auto_approve_on_label=True,
            reapprove=False,
            chatops_enabled=True,
            chatops_required_permission="maintain",
            approve_commands=["approve", "stamp"],
            unapprove_commands=["unapprove", "unstamp"],
            required_labels=[],
            required_title_patterns=[],
            allowed_users=[],
            allowed_teams=[],
            config_error=message or "Invalid configuration",
        )

    def is_pr_eligible(
        self,
        pr_labels: list[str],
        pr_title: str,
        pr_author: str,
        author_team_slugs: list[str] | None = None,
    ) -> tuple[bool, str | None]:
        """Check if a PR is eligible for auto-approval based on filters.

        All configured filters must pass (AND logic between filter types).
        Within each filter, any match is sufficient (OR logic).

        Args:
            pr_labels: List of label names on the PR
            pr_title: PR title
            pr_author: PR author's login
            author_team_slugs: List of team slugs the author belongs to (for team filter)

        Returns:
            Tuple of (is_eligible, reason_if_not_eligible)
        """
        # Check required labels filter
        if self.required_labels:
            if not any(label in self.required_labels for label in pr_labels):
                return (
                    False,
                    f"PR missing required label (one of: {', '.join(self.required_labels)})",
                )

        # Check required title patterns filter
        if self._compiled_title_patterns:
            if not isinstance(pr_title, str):
                return False, "PR title is not valid text"
            if len(pr_title) > MAX_PR_TITLE_LENGTH:
                return (
                    False,
                    f"PR title exceeds the {MAX_PR_TITLE_LENGTH}-character safety limit",
                )

            for pattern in self._compiled_title_patterns:
                try:
                    if pattern.search(
                        pr_title,
                        timeout=TITLE_PATTERN_TIMEOUT_SECONDS,
                        concurrent=True,
                    ):
                        break
                except TimeoutError:
                    timeout_ms = int(TITLE_PATTERN_TIMEOUT_SECONDS * 1000)
                    return (
                        False,
                        "PR title pattern evaluation could not complete within "
                        f"the {timeout_ms} ms safety limit",
                    )
                except regex.error:
                    return False, "PR title pattern evaluation failed safely"
            else:
                return False, "PR title does not match any required pattern"

        # Check allowed users/teams filter (if either is configured)
        if self.allowed_users or self.allowed_teams:
            # User is allowed if they're in allowed_users
            if pr_author in self.allowed_users:
                return True, None

            # Or if they're a member of an allowed team
            if self.allowed_teams and author_team_slugs:
                for team in self.allowed_teams:
                    # Support both "org/team" and "team" formats
                    team_slug = team.split("/")[-1] if "/" in team else team
                    if team_slug in author_team_slugs:
                        return True, None

            # Neither user nor team matched
            if self.allowed_users and self.allowed_teams:
                return False, "PR author not in allowed users or teams"
            elif self.allowed_users:
                return False, f"PR author not in allowed users: {', '.join(self.allowed_users)}"
            else:
                return False, "PR author not a member of any allowed team"

        return True, None

    def needs_team_check(self, pr_author: str) -> bool:
        """Check if team membership verification is needed for this author.

        Args:
            pr_author: PR author's login

        Returns:
            True if team check is needed (author not in allowed_users but teams configured)
        """
        if not self.allowed_teams:
            return False
        if self.allowed_users and pr_author in self.allowed_users:
            return False
        return True

    @classmethod
    def _get_defaults(cls) -> dict[str, Any]:
        """Get default repo config values.

        Checks settings.toml for overrides, falls back to REPO_CONFIG_DEFAULTS.

        Returns:
            Dictionary of default configuration values.
        """
        defaults = REPO_CONFIG_DEFAULTS.copy()

        # Allow overriding defaults via settings.toml [defaults] section
        if settings.get("defaults"):
            settings_defaults = settings.defaults
            for key in defaults:
                if hasattr(settings_defaults, key):
                    defaults[key] = getattr(settings_defaults, key)

        return defaults

    @classmethod
    def from_toml(cls, toml_content: str) -> RepoConfig:
        """Parse TOML content and merge with app defaults.

        Uses dynaconf to properly merge repo-specific settings with
        the app-level defaults, allowing repos to override only
        the settings they need to change.

        Args:
            toml_content: Raw TOML content from repo's stampbot.toml

        Returns:
            RepoConfig with merged settings
        """
        import toml

        # Start with defaults
        defaults = cls._get_defaults()

        # Parse the repo-specific TOML
        repo_settings: dict[str, Any] = {}
        if toml_content:
            try:
                repo_settings = toml.loads(toml_content)
            except Exception as e:
                raise ValueError(f"Invalid TOML content: {e}") from e

        # Merge: repo settings override defaults
        merged = {**defaults, **repo_settings}

        required_permission = merged.get("chatops_required_permission", "maintain")
        if required_permission not in REPO_PERMISSION_LEVELS:
            raise ValueError(
                "Invalid chatops_required_permission: "
                f"{required_permission}. "
                f"Valid values: {', '.join(REPO_PERMISSION_LEVELS)}"
            )

        title_patterns = merged.get("required_title_patterns", [])

        return cls(
            approval_labels=merged.get("approval_labels", []),
            auto_approve_on_label=merged.get("auto_approve_on_label", True),
            reapprove=merged.get("reapprove", False),
            chatops_enabled=merged.get("chatops_enabled", True),
            chatops_required_permission=required_permission,
            approve_commands=merged.get("approve_commands", ["approve", "stamp"]),
            unapprove_commands=merged.get("unapprove_commands", ["unapprove", "unstamp"]),
            required_labels=merged.get("required_labels", []),
            required_title_patterns=title_patterns,
            allowed_users=merged.get("allowed_users", []),
            allowed_teams=merged.get("allowed_teams", []),
        )

    @classmethod
    def default(cls) -> RepoConfig:
        """Return default configuration from app settings.

        Returns:
            RepoConfig using app-level defaults
        """
        defaults = cls._get_defaults()
        return cls(
            approval_labels=defaults["approval_labels"],
            auto_approve_on_label=defaults["auto_approve_on_label"],
            reapprove=defaults["reapprove"],
            chatops_enabled=defaults["chatops_enabled"],
            chatops_required_permission=defaults["chatops_required_permission"],
            approve_commands=defaults["approve_commands"],
            unapprove_commands=defaults["unapprove_commands"],
            required_labels=defaults["required_labels"],
            required_title_patterns=defaults["required_title_patterns"],
            allowed_users=defaults["allowed_users"],
            allowed_teams=defaults["allowed_teams"],
        )

    @classmethod
    def default_or_config_error(cls) -> RepoConfig:
        """Return configured defaults, failing closed if they are invalid.

        Returns:
            Configured defaults, or a built-in config carrying config_error.
        """
        try:
            return cls.default()
        except ValueError as e:
            return cls.fail_closed(f"Invalid service default configuration: {e}")
