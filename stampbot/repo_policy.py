# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Repository policy resolution with a short-lived in-memory cache."""

from cachetools import TTLCache
from fastapi.concurrency import run_in_threadpool
from opentelemetry.trace import Span

from stampbot.config import (
    DEFAULTS_INVALID,
    REPO_CONFIG_DEFAULTS,
    RedactedConfigError,
    RepoConfig,
)
from stampbot.github_client import github_client
from stampbot.logger import get_logger
from stampbot.metrics import repo_config_loads_total
from stampbot.telemetry import add_span_attributes, create_span, set_span_error, set_span_ok

logger = get_logger(__name__)

POLICY_FILE = "stampbot.toml"
# Upper bound on repositories whose policy one replica keeps in memory.
POLICY_CACHE_SIZE = 1024

PolicyKey = tuple[int, str, str]


class RepoPolicyResolver:
    """Find the policy that applies to a repository and remember valid results.

    Lookup order is the repository's own ``stampbot.toml`` on its default
    branch, then the owner organization's ``.github`` repository, then the
    service-wide defaults. Valid results, including "no file found", stay in
    memory per installation, repository, and default branch for
    ``cache_seconds``. Invalid policy and read failures are never cached, so a
    corrected file applies on the next event.

    The resolver is only used from the event loop, so the cache needs no lock.
    """

    def __init__(self, cache_seconds: int) -> None:
        """Create a resolver.

        Args:
            cache_seconds: Seconds to keep a valid result. Zero disables caching.
        """
        self._cache: TTLCache[PolicyKey, RepoConfig] | None = (
            TTLCache(maxsize=POLICY_CACHE_SIZE, ttl=cache_seconds) if cache_seconds > 0 else None
        )

    async def get(
        self,
        installation_id: int,
        repo_full_name: str,
        default_branch: str,
        owner_login: str | None,
        owner_type: str | None,
    ) -> RepoConfig:
        """Return the effective policy for a repository.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            default_branch: Repository default branch
            owner_login: Repository owner login
            owner_type: Repository owner type (Organization/User)

        Returns:
            RepoConfig instance. ``config_error`` is set when automation must stop.
        """
        key: PolicyKey = (installation_id, repo_full_name, default_branch)
        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                repo_config_loads_total.labels(status="cached").inc()
                return cached

        repo_config = await self._load(
            installation_id, repo_full_name, default_branch, owner_login, owner_type
        )

        if self._cache is not None and repo_config.config_error is None:
            self._cache[key] = repo_config

        return repo_config

    async def _load(
        self,
        installation_id: int,
        repo_full_name: str,
        default_branch: str,
        owner_login: str | None,
        owner_type: str | None,
    ) -> RepoConfig:
        """Read policy from GitHub without consulting the cache.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            default_branch: Repository default branch
            owner_login: Repository owner login
            owner_type: Repository owner type (Organization/User)

        Returns:
            RepoConfig instance (default if file not found)
        """
        with create_span(
            "webhook.get_repo_config",
            {"github.repo": repo_full_name, "github.ref": default_branch or "default"},
        ) as span:
            try:
                content = await run_in_threadpool(
                    github_client.get_repo_file,
                    installation_id,
                    repo_full_name,
                    POLICY_FILE,
                    default_branch,
                )

                if content:
                    return self._parse(
                        span,
                        content,
                        where=f"stampbot.toml in {repo_full_name}",
                        source_repo=repo_full_name,
                        owner_login=owner_login,
                        owner_type=owner_type,
                    )

                org_repo_full_name = None
                if (
                    owner_type == "Organization"
                    and owner_login
                    and repo_full_name != f"{owner_login}/.github"
                ):
                    org_repo_full_name = f"{owner_login}/.github"
                    org_content = await run_in_threadpool(
                        github_client.get_repo_file,
                        installation_id,
                        org_repo_full_name,
                        POLICY_FILE,
                        None,
                        missing_repository_is_optional=True,
                    )
                    if org_content:
                        return self._parse(
                            span,
                            org_content,
                            where=(
                                f"the organization policy (stampbot.toml in {org_repo_full_name})"
                            ),
                            source_repo=org_repo_full_name,
                            owner_login=owner_login,
                            owner_type=owner_type,
                        )

                repo_config = RepoConfig.default_or_config_error()
                if not repo_config.config_error:
                    try:
                        repo_config.check_team_owner(owner_login, owner_type)
                    except ValueError as e:
                        repo_config = RepoConfig.fail_closed(str(e), detail=_detail(e))
                if repo_config.config_error:
                    error = ValueError(repo_config.config_error)
                    repo_config_loads_total.labels(status="error").inc()
                    logger.warning(
                        "Invalid service default configuration for %s: %s",
                        repo_full_name,
                        repo_config.config_error_detail,
                        extra={"repo": repo_full_name, "error": repo_config.config_error_detail},
                    )
                    add_span_attributes(
                        span,
                        {
                            "config.result": "error",
                            "config.error": repo_config.config_error_detail,
                            "config.source_repo": "service_defaults",
                        },
                    )
                    set_span_error(span, error)
                    return repo_config

                repo_config_loads_total.labels(status="default").inc()
                _warn_about_author_filter(repo_config, owner_type)
                logger.info(
                    "No stampbot.toml found in %s%s, using defaults",
                    repo_full_name,
                    f" or {org_repo_full_name}" if org_repo_full_name else "",
                    extra={"repo": repo_full_name, "org_repo": org_repo_full_name},
                )
                add_span_attributes(span, {"config.result": "default"})
                set_span_ok(span)
                return repo_config

            except Exception as e:
                repo_config_loads_total.labels(status="error").inc()
                logger.warning(
                    "Error loading config from %s: %s, disabling automation",
                    repo_full_name,
                    e,
                    extra={"repo": repo_full_name, "error": str(e)},
                )
                add_span_attributes(span, {"config.result": "error"})
                set_span_error(span, e)
                return RepoConfig.fail_closed(
                    "Unable to load Stampbot configuration; automation is disabled"
                )

    @staticmethod
    def _parse(
        span: Span | None,
        toml_content: str,
        *,
        where: str,
        source_repo: str,
        owner_login: str | None,
        owner_type: str | None,
    ) -> RepoConfig:
        """Parse policy content with metrics and tracing.

        Args:
            span: Active tracing span (None if tracing disabled)
            toml_content: Raw TOML content
            where: Plain description of the policy file, used in messages
            source_repo: Repository name where the policy was found
            owner_login: Owner of the repository the event came from
            owner_type: Owner account type (Organization or User)

        Returns:
            RepoConfig instance (fail-closed with config_error on invalid content)
        """
        try:
            try:
                repo_config = RepoConfig.from_toml(toml_content)
            except ValueError as e:
                # A policy file inherits every key it leaves out, so the error
                # may come from the service defaults. Parse the file again over
                # the built-in defaults, which are always valid: if it still
                # fails, the file is wrong; if not, an inherited default is.
                try:
                    RepoConfig.from_toml(toml_content, defaults=REPO_CONFIG_DEFAULTS)
                except ValueError as file_error:
                    raise ValueError(f"Stampbot can't use {where}: {file_error}") from e
                logger.warning(
                    "Invalid service default configuration for %s: %s",
                    source_repo,
                    e,
                    extra={"repo": source_repo, "error": str(e)},
                )
                raise RedactedConfigError(DEFAULTS_INVALID, detail=str(e)) from e
            if not repo_config.author_filter_from_defaults:
                repo_config.author_filter_source = where
            repo_config.check_team_owner(owner_login, owner_type)
        except ValueError as e:
            repo_config_loads_total.labels(status="error").inc()
            add_span_attributes(
                span,
                {
                    "config.result": "error",
                    "config.error": _detail(e),
                    "config.source_repo": source_repo,
                },
            )
            set_span_error(span, e)
            if isinstance(e, RedactedConfigError):
                logger.warning(
                    "Invalid service default configuration for %s: %s",
                    source_repo,
                    e.detail,
                    extra={"repo": source_repo, "error": e.detail},
                )
            return RepoConfig.fail_closed(str(e), detail=_detail(e))

        repo_config_loads_total.labels(status="found").inc()
        _warn_about_author_filter(repo_config, owner_type)
        add_span_attributes(span, {"config.result": "found", "config.source_repo": source_repo})
        set_span_ok(span)
        return repo_config


def _warn_about_author_filter(repo_config: RepoConfig, owner_type: str | None) -> None:
    """Log what a repository owner should change in the author filter.

    Args:
        repo_config: Parsed policy.
        owner_type: Repository owner account type (Organization or User).
    """
    source = repo_config.author_filter_source
    for setting in repo_config.deprecated_settings:
        logger.warning(
            "%s uses the deprecated %s setting. Move its entries to allowed_authors.",
            source,
            setting,
            extra={"config_source": source, "setting": setting},
        )
    if owner_type == "User" and repo_config.allowed_authors.teams:
        teams = ", ".join(team.display() for team in repo_config.allowed_authors.teams)
        logger.warning(
            "%s lists teams (%s), but GitHub doesn't support teams on personal accounts, "
            "so Stampbot ignores them. List people by login instead.",
            source,
            teams,
            extra={"config_source": source, "teams": teams},
        )


def _detail(error: ValueError) -> str:
    """Return the full text of a policy error, for operator logs.

    Args:
        error: Error raised while loading policy.

    Returns:
        The unredacted detail when the error carries one, else its message.
    """
    return error.detail if isinstance(error, RedactedConfigError) else str(error)
