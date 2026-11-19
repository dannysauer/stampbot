# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Configuration management using dynaconf."""

from __future__ import annotations

from typing import Any

import regex  # type: ignore[import-untyped]
from dynaconf import Dynaconf  # type: ignore[import-untyped]

from stampbot.principals import MAX_PRINCIPALS, PrincipalSet, parse_principals

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
    # PR author must be one of these logins or a member of one of these teams.
    # CODEOWNERS syntax: "renovate[bot]", "@octocat", or "@org/team-slug".
    "allowed_authors": [],
    # Deprecated aliases of allowed_authors: logins only, and teams only.
    "allowed_users": [],
    "allowed_teams": [],
}
# Where policy came from when no repository or organization file supplied it.
SERVICE_DEFAULTS_SOURCE = "Stampbot's service-wide defaults"
# What a repository owner can do about a problem in the service-wide defaults.
OPERATOR_DEFAULTS_FIX = (
    "Only your Stampbot operator can change the service-wide defaults, so ask them to "
    "fix it, or set allowed_authors in this repository's stampbot.toml to replace it."
)
# What repository users see when the service-wide defaults are invalid. The
# details go to the operator's logs instead, because they can name settings,
# such as default teams, that only the operator should see.
DEFAULTS_INVALID = (
    "Stampbot's service-wide defaults are invalid, so Stampbot is turned off for this "
    "repository. Only your Stampbot operator can fix them, and Stampbot's logs have "
    "the details."
)


class RedactedConfigError(ValueError):
    """A configuration error whose message hides details from repository users.

    Attributes:
        detail: Full error for operator logs.
    """

    def __init__(self, message: str, detail: str) -> None:
        """Store the redacted message and the full detail.

        Args:
            message: Text safe to show repository users.
            detail: Full error for operator logs.
        """
        super().__init__(message)
        self.detail = detail


# Ineligibility reason when the pull request author fails the author filter.
AUTHOR_NOT_ALLOWED = "PR author is not in allowed_authors"

# Settings that together make up the pull request author filter. A policy file
# that sets any of them replaces all of them from the service defaults.
AUTHOR_FILTER_KEYS = ("allowed_authors", "allowed_users", "allowed_teams")
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


DEFAULT_REPO_CONFIG_CACHE_SECONDS = 300


def repo_config_cache_seconds() -> int:
    """Return how long a repository's parsed policy stays cached, in seconds.

    Returns:
        A non-negative integer. Zero disables the cache.

    Raises:
        ValueError: If ``repo_config_cache_seconds`` is not a non-negative integer.
    """
    raw = settings.get("repo_config_cache_seconds", DEFAULT_REPO_CONFIG_CACHE_SECONDS)
    # Dynaconf parses environment values, so ``false`` arrives as a bool and
    # ``1.9`` as a float. Neither is an integer setting, and int() would accept
    # both silently.
    if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
        raise ValueError(f"repo_config_cache_seconds must be an integer, got {raw!r}")
    try:
        seconds = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"repo_config_cache_seconds must be an integer, got {raw!r}") from error
    if seconds < 0:
        raise ValueError(f"repo_config_cache_seconds must be 0 or greater, got {raw!r}")
    return seconds


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
        allowed_authors: list[str] | None = None,
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
            allowed_authors: Pull request author logins and ``@org/team`` entries
                allowed for label approval (any match)
            allowed_users: Deprecated alias of ``allowed_authors`` for logins
            allowed_teams: Deprecated alias of ``allowed_authors`` for teams;
                bare slugs mean the repository owner's organization
            config_error: Config error message if config was invalid
        """
        self.approval_labels = approval_labels
        self.auto_approve_on_label = auto_approve_on_label
        self.reapprove = reapprove
        self.chatops_enabled = chatops_enabled
        self.chatops_required_permission = self._validate_required_permission(
            chatops_required_permission
        )
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
        self.allowed_authors, self.deprecated_settings = self._parse_author_filter(
            allowed_authors, allowed_users, allowed_teams
        )
        # True when the author filter came from service defaults, not a policy file.
        self.author_filter_from_defaults = False
        # Plain description of where the author filter came from, for messages.
        self.author_filter_source = SERVICE_DEFAULTS_SOURCE
        self.config_error = config_error
        # Full error for operator logs; config_error may be a redacted summary.
        self.config_error_detail = config_error

    @staticmethod
    def _parse_author_filter(
        allowed_authors: object,
        allowed_users: object,
        allowed_teams: object,
    ) -> tuple[PrincipalSet, tuple[str, ...]]:
        """Merge the author filter and its deprecated aliases into one set.

        Args:
            allowed_authors: ``allowed_authors`` setting, or None.
            allowed_users: Deprecated ``allowed_users`` setting, or None.
            allowed_teams: Deprecated ``allowed_teams`` setting, or None.

        Returns:
            The merged entries and the names of deprecated settings in use.

        Raises:
            ValueError: If a setting is invalid or the merged list is too long.
        """
        authors = parse_principals(
            [] if allowed_authors is None else allowed_authors, "allowed_authors"
        )
        users = parse_principals(
            [] if allowed_users is None else allowed_users, "allowed_users", allow_teams=False
        )
        teams = parse_principals(
            [] if allowed_teams is None else allowed_teams,
            "allowed_teams",
            bare_name_is_team=True,
        )
        merged = authors.union(users).union(teams)
        if len(merged) > MAX_PRINCIPALS:
            raise ValueError(
                f"allowed_authors, allowed_users, and allowed_teams contain too many "
                f"entries together (maximum: {MAX_PRINCIPALS})"
            )
        deprecated = tuple(
            name
            for name, entries in (("allowed_users", users), ("allowed_teams", teams))
            if entries
        )
        return merged, deprecated

    def check_team_owner(self, owner_login: str | None, owner_type: str | None = None) -> None:
        """Reject an author-filter team outside the repository owner's organization.

        A repository owned by a personal account skips the check: GitHub has no
        teams there, so Stampbot ignores team entries and says so instead.

        Args:
            owner_login: Repository owner login.
            owner_type: Repository owner account type (Organization or User).

        Raises:
            ValueError: If a team names another organization.
        """
        if owner_type == "User":
            return
        try:
            self.allowed_authors.check_team_owner(owner_login, self.author_filter_source)
        except ValueError as error:
            if self.author_filter_from_defaults:
                # Repository contributors see this error, so don't name the
                # operator's default teams.
                raise RedactedConfigError(
                    f"A team in {SERVICE_DEFAULTS_SOURCE} belongs to an organization other "
                    f"than this repository's owner, {owner_login or 'unknown'!r}, so "
                    f"Stampbot can't check it. {OPERATOR_DEFAULTS_FIX}",
                    detail=str(error),
                ) from error
            raise

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
    def _validate_required_permission(permission: object) -> str:
        """Require the ChatOps permission threshold to be a supported value.

        Args:
            permission: Parsed application or repository setting.

        Returns:
            The validated permission name.

        Raises:
            ValueError: If the permission is not one of GitHub's repository levels.
        """
        if not isinstance(permission, str) or permission not in REPO_PERMISSION_LEVELS:
            raise ValueError(
                "Invalid chatops_required_permission: "
                f"{permission}. "
                f"Valid values: {', '.join(REPO_PERMISSION_LEVELS)}"
            )
        return permission

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
    def fail_closed(cls, message: str, detail: str | None = None) -> RepoConfig:
        """Return a valid config object that cannot authorize automation.

        This fallback deliberately uses code-defined values rather than
        application settings. The attached config error causes webhook
        handlers to stop before evaluating any of the policy fields.

        Args:
            message: Configuration error that caused the fallback.
            detail: Fuller error for operator logs, when ``message`` is redacted.

        Returns:
            Structurally valid built-in configuration with config_error set.
        """
        config = cls(
            approval_labels=["autoapprove", "stamp"],
            auto_approve_on_label=True,
            reapprove=False,
            chatops_enabled=True,
            chatops_required_permission="maintain",
            approve_commands=["approve", "stamp"],
            unapprove_commands=["unapprove", "unstamp"],
            required_labels=[],
            required_title_patterns=[],
            config_error=message or "Invalid configuration",
        )
        if detail:
            config.config_error_detail = detail
        return config

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

        # Check the pull request author filter (if configured)
        if self.allowed_authors:
            if self.allowed_authors.has_login(pr_author):
                return True, None
            if author_team_slugs and self.allowed_authors.has_any_team(author_team_slugs):
                return True, None
            return False, AUTHOR_NOT_ALLOWED

        return True, None

    def needs_team_check(self, pr_author: str) -> bool:
        """Check if team membership verification is needed for this author.

        Args:
            pr_author: PR author's login

        Returns:
            True if teams are listed and the author isn't listed directly.
        """
        if not self.allowed_authors.teams:
            return False
        return not self.allowed_authors.has_login(pr_author)

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
    def from_toml(cls, toml_content: str, defaults: dict[str, Any] | None = None) -> RepoConfig:
        """Parse TOML content and merge with app defaults.

        Uses dynaconf to properly merge repo-specific settings with
        the app-level defaults, allowing repos to override only
        the settings they need to change.

        Args:
            toml_content: Raw TOML content from repo's stampbot.toml
            defaults: Values for keys the file leaves out. Uses the service
                defaults when omitted.

        Returns:
            RepoConfig with merged settings
        """
        import toml

        # Start with defaults
        defaults = cls._get_defaults() if defaults is None else dict(defaults)

        # Parse the repo-specific TOML
        repo_settings: dict[str, Any] = {}
        if toml_content:
            try:
                repo_settings = toml.loads(toml_content)
            except Exception as e:
                raise ValueError(f"Invalid TOML content: {e}") from e

        # Merge: repo settings override defaults. The author filter keys act as
        # one setting, so a file that sets any of them drops all three defaults.
        author_filter_from_defaults = not any(key in repo_settings for key in AUTHOR_FILTER_KEYS)
        if not author_filter_from_defaults:
            defaults = {
                key: value for key, value in defaults.items() if key not in AUTHOR_FILTER_KEYS
            }
        merged = {**defaults, **repo_settings}

        title_patterns = merged.get("required_title_patterns", [])

        repo_config = cls(
            approval_labels=merged.get("approval_labels", []),
            auto_approve_on_label=merged.get("auto_approve_on_label", True),
            reapprove=merged.get("reapprove", False),
            chatops_enabled=merged.get("chatops_enabled", True),
            chatops_required_permission=merged.get(
                "chatops_required_permission",
                "maintain",
            ),
            approve_commands=merged.get("approve_commands", ["approve", "stamp"]),
            unapprove_commands=merged.get("unapprove_commands", ["unapprove", "unstamp"]),
            required_labels=merged.get("required_labels", []),
            required_title_patterns=title_patterns,
            allowed_authors=merged.get("allowed_authors"),
            allowed_users=merged.get("allowed_users"),
            allowed_teams=merged.get("allowed_teams"),
        )
        repo_config.author_filter_from_defaults = author_filter_from_defaults
        if not author_filter_from_defaults:
            # An empty deprecated key in a policy file still replaces the
            # default filter, so its presence counts as use.
            repo_config.deprecated_settings = tuple(
                key for key in ("allowed_users", "allowed_teams") if key in repo_settings
            )
        return repo_config

    @classmethod
    def default(cls) -> RepoConfig:
        """Return default configuration from app settings.

        Returns:
            RepoConfig using app-level defaults
        """
        defaults = cls._get_defaults()
        repo_config = cls(
            approval_labels=defaults["approval_labels"],
            auto_approve_on_label=defaults["auto_approve_on_label"],
            reapprove=defaults["reapprove"],
            chatops_enabled=defaults["chatops_enabled"],
            chatops_required_permission=defaults["chatops_required_permission"],
            approve_commands=defaults["approve_commands"],
            unapprove_commands=defaults["unapprove_commands"],
            required_labels=defaults["required_labels"],
            required_title_patterns=defaults["required_title_patterns"],
            allowed_authors=defaults["allowed_authors"],
            allowed_users=defaults["allowed_users"],
            allowed_teams=defaults["allowed_teams"],
        )
        repo_config.author_filter_from_defaults = True
        return repo_config

    @classmethod
    def default_or_config_error(cls) -> RepoConfig:
        """Return configured defaults, failing closed if they are invalid.

        Returns:
            Configured defaults, or a built-in config carrying config_error.
        """
        try:
            return cls.default()
        except ValueError as e:
            return cls.fail_closed(
                DEFAULTS_INVALID, detail=f"Invalid service default configuration: {e}"
            )
