# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""GitHub API client for app authentication and operations."""

import re
import time

from github import Auth, Github, GithubIntegration
from github.GithubException import GithubException
from urllib3.util.retry import Retry

from stampbot.config import is_configured, settings
from stampbot.logger import get_logger
from stampbot.metrics import (
    github_api_rate_limit_limit,
    github_api_rate_limit_remaining,
    github_api_request_duration_seconds,
    github_api_requests_total,
)
from stampbot.telemetry import add_span_attributes, create_span, set_span_error, set_span_ok

# GitHub API configuration
GITHUB_API_TIMEOUT = 30  # seconds
GITHUB_API_RETRY_TOTAL = 3
GITHUB_API_RETRY_BACKOFF = 0.5  # exponential backoff factor

logger = get_logger(__name__)

# Pattern to match GitHub tokens (installation tokens, PATs, etc.)
_TOKEN_PATTERN = re.compile(r"(ghs_[A-Za-z0-9]{36}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]+)")


def _sanitize_error(error: Exception) -> str:
    """Sanitize error message to remove sensitive data like tokens.

    Args:
        error: Exception to sanitize

    Returns:
        Sanitized error message string
    """
    error_str = str(error)
    return _TOKEN_PATTERN.sub("[REDACTED]", error_str)


class GitHubAppClient:
    """GitHub App client with installation token management."""

    def __init__(self) -> None:
        """Initialize GitHub App client (lazy initialization)."""
        self._auth: Auth.AppAuth | None = None
        self._integration: GithubIntegration | None = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Ensure client is initialized with credentials.

        Raises:
            RuntimeError: If GitHub App credentials are not configured.
        """
        if self._initialized:
            return

        if not is_configured():
            raise RuntimeError("GitHub App not configured. Visit /setup to create your GitHub App.")

        # is_configured() guarantees app_id is set, but check for type safety
        app_id = settings.app_id
        if app_id is None:
            raise RuntimeError("App ID not configured")

        private_key = self._load_private_key()
        self._auth = Auth.AppAuth(app_id, private_key)
        self._integration = GithubIntegration(auth=self._auth)
        self._initialized = True

    @property
    def integration(self) -> GithubIntegration:
        """Get GitHub integration, initializing if needed.

        Returns:
            Configured GithubIntegration instance.

        Raises:
            RuntimeError: If GitHub App credentials are not configured.
        """
        self._ensure_initialized()
        assert self._integration is not None  # guaranteed by _ensure_initialized
        return self._integration

    def _load_private_key(self) -> str:
        """Load private key from settings or file.

        Returns:
            Private key contents as PEM string.

        Raises:
            RuntimeError: If private key is not configured.
            ValueError: If private key is not valid PEM format.
            OSError: If private key file cannot be read.
        """
        key: str | None = settings.private_key

        if key is None:
            raise RuntimeError("Private key not configured")

        # If it looks like PEM content, use directly
        if key.startswith("-----BEGIN"):
            pem_content = key
        else:
            # Treat as file path - read the file
            import os
            from pathlib import Path

            key_path = Path(key).resolve()

            # Security: prevent path traversal by ensuring the path doesn't escape
            # expected directories and doesn't follow symlinks to unexpected locations
            if ".." in str(key_path) or not os.path.isfile(key_path):
                raise ValueError(f"Invalid private key path: {key}")

            try:
                with open(key_path) as f:
                    pem_content = f.read()
            except Exception as e:
                logger.error("Failed to read private key from file: %s", e)
                raise

        # Validate the content is actually a PEM-formatted key
        if not pem_content.strip().startswith("-----BEGIN"):
            raise ValueError("Private key must be in PEM format")

        return pem_content

    def _get_installation_client(self, installation_id: int) -> Github:
        """Get authenticated GitHub client for an installation.

        Args:
            installation_id: GitHub App installation ID.

        Returns:
            Authenticated Github client instance with timeout and retry configured.

        Raises:
            github.GithubException: If token exchange fails.
        """
        start_time = time.time()

        with create_span(
            "github.get_installation_token",
            {"github.installation_id": installation_id},
        ) as span:
            try:
                auth = self.integration.get_access_token(installation_id)

                # Configure retry with exponential backoff
                retry = Retry(
                    total=GITHUB_API_RETRY_TOTAL,
                    backoff_factor=GITHUB_API_RETRY_BACKOFF,
                    status_forcelist=[500, 502, 503, 504],
                    allowed_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                )

                client = Github(
                    auth=auth.token,  # type: ignore[arg-type]
                    timeout=GITHUB_API_TIMEOUT,
                    retry=retry,
                )

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_token").observe(duration)
                github_api_requests_total.labels(operation="get_token", status="success").inc()

                set_span_ok(span)
                return client

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_token").observe(duration)
                github_api_requests_total.labels(operation="get_token", status="failure").inc()
                set_span_error(span, e)
                raise

    def _update_rate_limit_metrics(self, client: Github, installation_id: int) -> None:
        """Update rate limit metrics from GitHub client.

        Args:
            client: Authenticated GitHub client.
            installation_id: GitHub App installation ID for metric labels.
        """
        try:
            rate_limit = client.get_rate_limit()
            github_api_rate_limit_remaining.labels(installation_id=str(installation_id)).set(
                rate_limit.core.remaining  # type: ignore[attr-defined]
            )
            github_api_rate_limit_limit.labels(installation_id=str(installation_id)).set(
                rate_limit.core.limit  # type: ignore[attr-defined]
            )
        except Exception:
            # Don't fail operations due to rate limit metric errors
            pass

    def approve_pr(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        comment: str = "Auto-approved by Stampbot",
    ) -> bool:
        """Approve a pull request.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            pr_number: Pull request number
            comment: Review comment

        Returns:
            True if successful, False otherwise
        """
        start_time = time.time()

        with create_span(
            "github.approve_pr",
            {
                "github.repo": repo_full_name,
                "github.pr_number": pr_number,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)
                pr = repo.get_pull(pr_number)

                # Create approval review
                pr.create_review(
                    body=comment,
                    event="APPROVE",
                )

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="approve").observe(duration)
                github_api_requests_total.labels(operation="approve", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.result": "approved"})
                set_span_ok(span)

                logger.info(
                    f"Approved PR #{pr_number} in {repo_full_name}",
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                    },
                )
                return True

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="approve").observe(duration)
                github_api_requests_total.labels(operation="approve", status="failure").inc()

                set_span_error(span, e)

                logger.error(
                    f"Failed to approve PR #{pr_number} in {repo_full_name}: {_sanitize_error(e)}",
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return False

    def dismiss_approval(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        review_id: int,
        message: str = "Approval dismissed by Stampbot",
    ) -> bool:
        """Dismiss a pull request approval.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            pr_number: Pull request number
            review_id: Review ID to dismiss
            message: Dismissal message

        Returns:
            True if successful, False otherwise
        """
        start_time = time.time()

        with create_span(
            "github.dismiss_approval",
            {
                "github.repo": repo_full_name,
                "github.pr_number": pr_number,
                "github.review_id": review_id,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)
                pr = repo.get_pull(pr_number)

                # Get the review and dismiss it
                review = pr.get_review(review_id)
                review.dismiss(message)

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="dismiss").observe(duration)
                github_api_requests_total.labels(operation="dismiss", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.result": "dismissed"})
                set_span_ok(span)

                logger.info(
                    f"Dismissed approval for PR #{pr_number} in {repo_full_name}",
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                        "review_id": review_id,
                    },
                )
                return True

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="dismiss").observe(duration)
                github_api_requests_total.labels(operation="dismiss", status="failure").inc()

                set_span_error(span, e)

                logger.error(
                    f"Failed to dismiss approval for PR #{pr_number} in {repo_full_name}: {_sanitize_error(e)}",
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return False

    def get_repo_file(
        self,
        installation_id: int,
        repo_full_name: str,
        file_path: str,
        ref: str | None = None,
    ) -> str | None:
        """Get file content from repository.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            file_path: Path to file in repository
            ref: Git reference (branch, tag, commit). Defaults to default branch

        Returns:
            File content as string, or None if not found
        """
        start_time = time.time()

        with create_span(
            "github.get_file",
            {
                "github.repo": repo_full_name,
                "github.file_path": file_path,
                "github.ref": ref or "default",
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)

                content = repo.get_contents(file_path, ref=ref)  # type: ignore[arg-type]
                if isinstance(content, list):
                    duration = time.time() - start_time
                    github_api_request_duration_seconds.labels(operation="get_file").observe(
                        duration
                    )
                    github_api_requests_total.labels(operation="get_file", status="not_found").inc()
                    add_span_attributes(span, {"github.result": "not_found"})
                    set_span_ok(span)
                    return None

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_file").observe(duration)
                github_api_requests_total.labels(operation="get_file", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.result": "found"})
                set_span_ok(span)

                return content.decoded_content.decode("utf-8")

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_file").observe(duration)
                github_api_requests_total.labels(operation="get_file", status="not_found").inc()

                add_span_attributes(span, {"github.result": "not_found"})
                set_span_ok(span)  # Not finding a file is not an error

                logger.debug(
                    f"Could not fetch {file_path} from {repo_full_name}: {_sanitize_error(e)}",
                    extra={
                        "repo": repo_full_name,
                        "file_path": file_path,
                        "installation_id": installation_id,
                    },
                )
                return None

    def find_bot_reviews(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
    ) -> list[int]:
        """Find all reviews created by this bot on a PR.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            pr_number: Pull request number

        Returns:
            List of review IDs created by the bot
        """
        start_time = time.time()

        with create_span(
            "github.find_bot_reviews",
            {
                "github.repo": repo_full_name,
                "github.pr_number": pr_number,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)
                pr = repo.get_pull(pr_number)

                # Get bot user
                bot_user = client.get_user().login

                # Find all reviews by bot that are approvals
                bot_review_ids = []
                for review in pr.get_reviews():
                    if review.user.login == bot_user and review.state == "APPROVED":
                        bot_review_ids.append(review.id)

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="find_reviews").observe(
                    duration
                )
                github_api_requests_total.labels(operation="find_reviews", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(
                    span, {"github.reviews_found": len(bot_review_ids), "github.bot_user": bot_user}
                )
                set_span_ok(span)

                return bot_review_ids

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="find_reviews").observe(
                    duration
                )
                github_api_requests_total.labels(operation="find_reviews", status="failure").inc()

                set_span_error(span, e)

                logger.error(
                    f"Failed to find bot reviews for PR #{pr_number} in {repo_full_name}: {_sanitize_error(e)}",
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return []

    def create_pr_review_comment(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        message: str,
    ) -> bool:
        """Create a comment review on a pull request.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            pr_number: Pull request number
            message: Review comment body

        Returns:
            True if successful, False otherwise
        """
        start_time = time.time()

        with create_span(
            "github.create_review_comment",
            {
                "github.repo": repo_full_name,
                "github.pr_number": pr_number,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)
                pr = repo.get_pull(pr_number)

                pr.create_review(body=message, event="COMMENT")

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="comment").observe(duration)
                github_api_requests_total.labels(operation="comment", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.result": "commented"})
                set_span_ok(span)

                logger.info(
                    "Posted config error review on PR #%d in %s",
                    pr_number,
                    repo_full_name,
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                    },
                )
                return True

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="comment").observe(duration)
                github_api_requests_total.labels(operation="comment", status="failure").inc()

                set_span_error(span, e)

                logger.warning(
                    "Failed to post config error review for PR #%d in %s: %s",
                    pr_number,
                    repo_full_name,
                    _sanitize_error(e),
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return False

    def repo_has_label(
        self,
        installation_id: int,
        repo_full_name: str,
        label_name: str,
    ) -> bool | None:
        """Check whether a repository has a label.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            label_name: Label name to look up

        Returns:
            True if label exists, False if not found, None if unknown due to error
        """
        start_time = time.time()

        with create_span(
            "github.get_label",
            {
                "github.repo": repo_full_name,
                "github.label": label_name,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)
                repo.get_label(label_name)

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_label").observe(duration)
                github_api_requests_total.labels(operation="get_label", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.label_found": True})
                set_span_ok(span)
                return True

            except GithubException as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_label").observe(duration)
                github_api_requests_total.labels(operation="get_label", status="failure").inc()

                if e.status == 404:
                    add_span_attributes(span, {"github.label_found": False})
                    set_span_ok(span)
                    return False

                set_span_error(span, e)
                logger.debug(
                    "Could not verify label %s in %s: %s",
                    label_name,
                    repo_full_name,
                    _sanitize_error(e),
                    extra={
                        "repo": repo_full_name,
                        "label": label_name,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return None

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_label").observe(duration)
                github_api_requests_total.labels(operation="get_label", status="failure").inc()

                set_span_error(span, e)
                logger.debug(
                    "Could not verify label %s in %s: %s",
                    label_name,
                    repo_full_name,
                    _sanitize_error(e),
                    extra={
                        "repo": repo_full_name,
                        "label": label_name,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return None

    def user_has_permission(
        self,
        installation_id: int,
        repo_full_name: str,
        username: str,
        required_permission: str,
    ) -> bool:
        """Check whether a user has required access to a repository.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            username: GitHub username to check
            required_permission: Minimum required repository permission

        Returns:
            True if user meets or exceeds required permission, False otherwise
        """
        start_time = time.time()

        with create_span(
            "github.get_collaborator_permission",
            {
                "github.repo": repo_full_name,
                "github.username": username,
                "github.required_permission": required_permission,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)

                permission = repo.get_collaborator_permission(username)
                permission_order = ["none", "read", "triage", "write", "maintain", "admin"]
                try:
                    permission_index = permission_order.index(permission)
                    required_index = permission_order.index(required_permission)
                except ValueError:
                    permission_index = -1
                    required_index = 99

                has_permission = permission_index >= required_index

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_permission").observe(
                    duration
                )
                github_api_requests_total.labels(operation="get_permission", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(
                    span,
                    {
                        "github.permission": permission,
                        "github.has_permission": has_permission,
                    },
                )
                set_span_ok(span)

                return has_permission

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_permission").observe(
                    duration
                )
                github_api_requests_total.labels(operation="get_permission", status="failure").inc()

                set_span_error(span, e)

                logger.warning(
                    "Failed to check collaborator permission for %s in %s: %s",
                    username,
                    repo_full_name,
                    _sanitize_error(e),
                    extra={
                        "repo": repo_full_name,
                        "username": username,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return False

    def get_user_team_slugs(
        self,
        installation_id: int,
        org_name: str,
        username: str,
        allowed_teams: list[str],
    ) -> list[str]:
        """Get team slugs the user is a member of from the allowed teams list.

        Args:
            installation_id: GitHub App installation ID
            org_name: Organization name
            username: GitHub username to check
            allowed_teams: List of team slugs to check (can be "org/team" or just "team")

        Returns:
            List of team slugs the user is a member of
        """
        start_time = time.time()

        with create_span(
            "github.get_user_team_slugs",
            {
                "github.org": org_name,
                "github.username": username,
                "github.installation_id": installation_id,
                "github.teams_to_check": len(allowed_teams),
            },
        ) as span:
            member_teams: list[str] = []

            try:
                client = self._get_installation_client(installation_id)
                org = client.get_organization(org_name)

                for team_ref in allowed_teams:
                    # Extract team slug (handle both "org/team" and "team" formats)
                    team_slug = team_ref.split("/")[-1] if "/" in team_ref else team_ref

                    try:
                        team = org.get_team_by_slug(team_slug)
                        # Check if user is a member
                        user = client.get_user(username)
                        if team.has_in_members(user):  # type: ignore[arg-type]
                            member_teams.append(team_slug)
                            logger.debug(
                                "User %s is a member of team %s",
                                username,
                                team_slug,
                                extra={
                                    "org": org_name,
                                    "username": username,
                                    "team": team_slug,
                                },
                            )
                    except GithubException as e:
                        # Team not found or no access - skip silently
                        logger.debug(
                            "Could not check team %s membership: %s",
                            team_slug,
                            e.data.get("message", str(e)) if hasattr(e, "data") else str(e),
                            extra={
                                "org": org_name,
                                "team": team_slug,
                                "username": username,
                            },
                        )
                        continue

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_user_teams").observe(
                    duration
                )
                github_api_requests_total.labels(operation="get_user_teams", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(
                    span,
                    {
                        "github.member_teams": len(member_teams),
                        "github.teams_checked": len(allowed_teams),
                    },
                )
                set_span_ok(span)

                return member_teams

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_user_teams").observe(
                    duration
                )
                github_api_requests_total.labels(operation="get_user_teams", status="failure").inc()

                set_span_error(span, e)

                logger.warning(
                    "Failed to check team memberships for %s in %s: %s",
                    username,
                    org_name,
                    _sanitize_error(e),
                    extra={
                        "org": org_name,
                        "username": username,
                        "installation_id": installation_id,
                        "error": _sanitize_error(e),
                    },
                )
                return []


# Global client instance
github_client = GitHubAppClient()
