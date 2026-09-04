# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""GitHub API client for app authentication and operations."""

import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Literal, TypedDict

from cachetools import TTLCache
from github import Auth, Github, GithubIntegration
from github.ContentFile import ContentFile
from github.GithubException import GithubException
from github.Repository import Repository
from urllib3.util.retry import Retry

from stampbot.config import is_configured, settings
from stampbot.installation_auth import InstrumentedInstallationAuth
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
MAX_PRIVATE_KEY_SIZE = 64 * 1024

# Installation credentials hold a token that PyGithub refreshes shortly before
# GitHub expires it. Entries expire an hour after creation, which bounds memory
# for installations that disappear and rotates the token about once an hour.
# The size also bounds the installation_id label on the rate-limit gauges, which
# keeps those metrics inside the repository's low-cardinality rule.
INSTALLATION_AUTH_CACHE_SIZE = 100
INSTALLATION_AUTH_CACHE_TTL = 3600  # seconds
# The App slug only changes when an operator renames the App.
BOT_LOGIN_TTL = 3600  # seconds

logger = get_logger(__name__)

# Pattern to match GitHub tokens (installation tokens, PATs, etc.)
_TOKEN_PATTERN = re.compile(r"(ghs_[A-Za-z0-9]{36}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]+)")


class BotReview(TypedDict):
    """Minimal review state needed for Stampbot approval decisions."""

    id: int
    state: str
    commit_id: str | None


def _sanitize_error(error: Exception) -> str:
    """Sanitize error message to remove sensitive data like tokens.

    Args:
        error: Exception to sanitize

    Returns:
        Sanitized error message string
    """
    error_str = str(error)
    return _TOKEN_PATTERN.sub("[REDACTED]", error_str)


def _get_repo_contents(
    repo: Repository,
    path: str,
    ref: str | None,
) -> list[ContentFile] | ContentFile:
    """Call PyGithub without passing ``None`` as an explicit Git ref."""
    if ref is None:
        return repo.get_contents(path)
    return repo.get_contents(path, ref=ref)


def _can_read_repo_root(repo: Repository, ref: str | None) -> bool:
    """Confirm that a file 404 did not conceal a repository, auth, or ref failure."""
    try:
        root = _get_repo_contents(repo, "", ref)
    except Exception:
        return False
    return isinstance(root, list)


def _repository_is_missing(repo: Repository) -> bool:
    """Confirm that GitHub returns 404 for the repository itself.

    Lazy repository objects never request ``GET /repos/{owner}/{repo}`` on their
    own, so a contents 404 cannot distinguish a missing file from a missing or
    inaccessible repository. This check settles that question with one request.
    """
    try:
        repo.complete()
    except GithubException as e:
        return e.status == 404
    except Exception:
        return False
    return False


NotFoundKind = Literal["repository", "file", "unconfirmed"]


def _classify_not_found(repo: Repository, ref: str | None, *, optional_repo: bool) -> NotFoundKind:
    """Decide what a contents 404 means.

    An optional fallback repository may itself be missing, which one repository
    request settles and which is the common case for organizations without a
    ``.github`` repository. A readable repository root proves the file is
    absent. Anything else stays unconfirmed and the caller fails closed.
    """
    if optional_repo and _repository_is_missing(repo):
        return "repository"
    if _can_read_repo_root(repo, ref):
        return "file"
    return "unconfirmed"


def _build_retry() -> Retry:
    """Build the urllib3 retry policy shared by every GitHub client."""
    return Retry(
        total=GITHUB_API_RETRY_TOTAL,
        backoff_factor=GITHUB_API_RETRY_BACKOFF,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    )


class GitHubAppClient:
    """GitHub App client with installation token management."""

    def __init__(self) -> None:
        """Initialize GitHub App client (lazy initialization)."""
        self._auth: Auth.AppAuth | None = None
        self._integration: GithubIntegration | None = None
        self._initialized = False
        # First use from several webhook threads must build the App credentials once.
        self._init_lock = threading.Lock()
        # Installation credentials are shared across webhook threads. PyGithub
        # caches the installation token inside the Auth object and refreshes it
        # shortly before expiry, so sharing it removes the token exchange from
        # every GitHub operation. Github clients themselves are never shared: a
        # PyGithub Requester keeps the in-flight request on one connection
        # object, so two threads using one client can swap each other's requests.
        self._installation_auths: TTLCache[int, InstrumentedInstallationAuth] = TTLCache(
            maxsize=INSTALLATION_AUTH_CACHE_SIZE,
            ttl=INSTALLATION_AUTH_CACHE_TTL,
        )
        self._installation_auths_lock = threading.Lock()
        # Installations that currently own a rate-limit gauge label set.
        self._rate_limit_installations: set[str] = set()
        self._bot_login_value: tuple[str, float] | None = None  # (login, expires_at)
        self._bot_login_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        """Ensure client is initialized with credentials.

        Raises:
            RuntimeError: If GitHub App credentials are not configured.
        """
        if self._initialized:
            return

        with self._init_lock:
            if self._initialized:
                return

            if not is_configured():
                raise RuntimeError(
                    "GitHub App not configured. Visit /setup to create your GitHub App."
                )

            # is_configured() guarantees app_id is set, but check for type safety
            app_id = settings.app_id
            if app_id is None:
                raise RuntimeError("App ID not configured")

            private_key = self._load_private_key()
            self._auth = Auth.AppAuth(app_id, private_key)
            self._integration = GithubIntegration(
                auth=self._auth,
                timeout=GITHUB_API_TIMEOUT,
                retry=_build_retry(),
            )
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

        # A non-PEM value is an operator-selected file path. It is not
        # request-controlled, so arbitrary relative and absolute paths are
        # intentional. Validate the opened target instead of pretending that
        # normalizing '..' segments creates a meaningful traversal boundary.
        if key.startswith("-----BEGIN"):
            pem_content = key
        else:
            try:
                with Path(key).expanduser().open(encoding="utf-8") as key_file:
                    file_metadata = os.fstat(key_file.fileno())
                    if not stat.S_ISREG(file_metadata.st_mode):
                        raise ValueError("Private key path must reference a regular file")
                    if file_metadata.st_size > MAX_PRIVATE_KEY_SIZE:
                        raise ValueError(f"Private key file exceeds {MAX_PRIVATE_KEY_SIZE} bytes")
                    pem_content = key_file.read(MAX_PRIVATE_KEY_SIZE + 1)
            except FileNotFoundError:
                raise ValueError("Invalid private key path: file does not exist") from None
            except OSError as e:
                logger.error("Failed to read private key file: %s", e)
                raise

        if len(pem_content.encode("utf-8")) > MAX_PRIVATE_KEY_SIZE:
            raise ValueError(f"Private key exceeds {MAX_PRIVATE_KEY_SIZE} bytes")

        # Validate a complete PEM private-key envelope. PyGithub performs the
        # cryptographic parsing when it creates AppAuth.
        pem_lines = pem_content.strip().splitlines()
        if len(pem_lines) < 3 or not pem_lines[0].startswith("-----BEGIN "):
            raise ValueError("Private key must be in PEM format")
        if not pem_lines[0].endswith("PRIVATE KEY-----"):
            raise ValueError("PEM value must contain a private key")
        expected_footer = pem_lines[0].replace("-----BEGIN ", "-----END ", 1)
        if pem_lines[-1] != expected_footer:
            raise ValueError("Private key PEM footer does not match its header")
        if not any(line.strip() for line in pem_lines[1:-1]):
            raise ValueError("Private key PEM body is empty")

        return pem_content

    def _new_client(self, installation_auth: Auth.AppInstallationAuth) -> Github:
        """Build a lazy client bound to installation credentials.

        Lazy clients build URLs for ``get_repo`` and ``get_pull`` without
        requesting the object, so each operation only pays for the requests it
        needs. Constructing the client also binds a requester to the
        credentials, which PyGithub requires before the token can be read.

        Args:
            installation_auth: Shared credentials for one installation.

        Returns:
            Github client instance with timeout and retry configured.
        """
        return Github(
            auth=installation_auth,
            timeout=GITHUB_API_TIMEOUT,
            retry=_build_retry(),
            lazy=True,
        )

    def _installation_auth(self, installation_id: int) -> InstrumentedInstallationAuth:
        """Return the shared credentials for an installation, creating them on first use.

        No network request happens here, so the cache lock is held only briefly.
        The token exchange happens when a caller reads ``token``, serialized per
        installation by the credentials themselves.

        Args:
            installation_id: GitHub App installation ID.

        Returns:
            Credentials whose token PyGithub exchanges on first read and
            refreshes before GitHub expires it.

        Raises:
            RuntimeError: If the GitHub App credentials are not configured.
        """
        with self._installation_auths_lock:
            installation_auth = self._installation_auths.get(installation_id)
            if installation_auth is None:
                # The App credentials must be loaded before an installation auth
                # can be derived from them.
                self._ensure_initialized()
                assert self._auth is not None  # guaranteed by _ensure_initialized
                installation_auth = InstrumentedInstallationAuth(self._auth, installation_id)
                self._installation_auths[installation_id] = installation_auth
                self._prune_rate_limit_metrics()
            return installation_auth

    def _prune_rate_limit_metrics(self) -> None:
        """Drop rate-limit gauge label sets for installations without live credentials.

        Prometheus keeps a child series for every label value ever set. Pruning
        whenever new credentials are created bounds the series count by the
        credential cache size, so installation churn cannot grow it without
        limit. Callers hold ``_installation_auths_lock``.
        """
        live = {str(installation_id) for installation_id in self._installation_auths}
        for stale in self._rate_limit_installations - live:
            # remove() is a no-op for a label set the gauge never held.
            github_api_rate_limit_remaining.remove(stale)
            github_api_rate_limit_limit.remove(stale)
        self._rate_limit_installations &= live

    def _get_installation_client(self, installation_id: int) -> Github:
        """Get an authenticated GitHub client for an installation.

        Every call builds a private lazy client on credentials shared per
        installation, so the token is reused while connection state stays
        private to the calling thread. Binding the client gives the credentials
        the requester PyGithub needs for an exchange. The token is then read
        here, before any repository request, so the first exchange or a due
        refresh happens now and an authentication failure never looks like a
        missing repository. A failed exchange leaves the credentials in place;
        the next read retries.

        Args:
            installation_id: GitHub App installation ID.

        Returns:
            Authenticated Github client instance with timeout and retry configured.

        Raises:
            github.GithubException: If token exchange fails.
        """
        installation_auth = self._installation_auth(installation_id)
        client = self._new_client(installation_auth)
        installation_auth.token  # noqa: B018
        return client

    def _update_rate_limit_metrics(self, client: Github, installation_id: int) -> None:
        """Update rate limit metrics from the client's last GitHub response.

        GitHub returns the installation's remaining quota in the
        ``x-ratelimit-*`` headers of every response, so this reads PyGithub's
        record of those headers instead of spending a request on
        ``GET /rate_limit``. The requester is read directly because
        ``Github.rate_limiting`` falls back to that request when no response
        has carried the headers yet.

        Args:
            client: Authenticated GitHub client.
            installation_id: GitHub App installation ID for metric labels.
        """
        try:
            remaining, limit = client.requester.rate_limiting
            if remaining < 0 or limit < 0:
                # PyGithub reports -1 until a response has carried the headers.
                return
            label = str(installation_id)
            github_api_rate_limit_remaining.labels(installation_id=label).set(remaining)
            github_api_rate_limit_limit.labels(installation_id=label).set(limit)
            with self._installation_auths_lock:
                self._rate_limit_installations.add(label)
        except Exception:
            # Don't fail operations due to rate limit metric errors
            pass

    def _bot_login(self) -> str:
        """Return the App's bot login, such as ``stampbot[bot]``.

        The slug is read through the JWT-authenticated App endpoint about once
        an hour, because installation tokens cannot call ``GET /user`` and the
        slug only changes when the App is renamed.

        Returns:
            Login GitHub attributes to reviews created by this App.
        """
        with self._bot_login_lock:
            now = time.monotonic()
            if self._bot_login_value is not None and self._bot_login_value[1] > now:
                return self._bot_login_value[0]
            login = f"{self.integration.get_app().slug}[bot]"
            self._bot_login_value = (login, now + BOT_LOGIN_TTL)
            return login

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

    def get_pr_head_sha(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
    ) -> str | None:
        """Get the current head SHA for a pull request.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            pr_number: Pull request number

        Returns:
            Current pull request head SHA, or None on error.
        """
        start_time = time.time()

        with create_span(
            "github.get_pr_head_sha",
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
                head_sha: str = pr.head.sha

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_pull").observe(duration)
                github_api_requests_total.labels(operation="get_pull", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.head_sha": head_sha})
                set_span_ok(span)

                return head_sha

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_pull").observe(duration)
                github_api_requests_total.labels(operation="get_pull", status="failure").inc()

                set_span_error(span, e)

                logger.error(
                    "Failed to get head SHA for PR #%s in %s: %s",
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
                return None

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
                try:
                    review.dismiss(message)
                except GithubException as e:
                    # GitHub answers 422 when a review is already dismissed,
                    # which two replicas cleaning up the same duplicate reach on
                    # purpose. 422 also covers other validation failures, so the
                    # review's state is read back before treating it as done.
                    if e.status != 422 or pr.get_review(review_id).state != "DISMISSED":
                        raise
                    logger.info(
                        "Review %d on PR #%d in %s was already dismissed",
                        review_id,
                        pr_number,
                        repo_full_name,
                        extra={
                            "repo": repo_full_name,
                            "pr_number": pr_number,
                            "review_id": review_id,
                        },
                    )

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
                    "Failed to dismiss approval for PR #%s in %s: %s",
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

    def get_repo_file(
        self,
        installation_id: int,
        repo_full_name: str,
        file_path: str,
        ref: str | None = None,
        *,
        missing_repository_is_optional: bool = False,
    ) -> str | None:
        """Get file content from repository.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            file_path: Path to file in repository
            ref: Git reference (branch, tag, commit). Defaults to default branch
            missing_repository_is_optional: Treat a repository-level 404 as not
                found. This is only for optional fallback repositories.

        Returns:
            File content as string, or None if not found
        """
        start_time = time.time()
        repo: Repository | None = None

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
                # Lazy: this builds the repository URL without a request, so
                # ``repo`` stays None only when authentication itself failed.
                repo = client.get_repo(repo_full_name)

                content = _get_repo_contents(repo, file_path, ref)
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

            except GithubException as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_file").observe(duration)
                not_found: NotFoundKind = "unconfirmed"
                if e.status == 404 and repo is not None:
                    not_found = _classify_not_found(
                        repo, ref, optional_repo=missing_repository_is_optional
                    )
                repository_is_optionally_missing = not_found == "repository"
                file_is_confirmed_missing = not_found == "file"
                if repository_is_optionally_missing or file_is_confirmed_missing:
                    github_api_requests_total.labels(operation="get_file", status="not_found").inc()

                    add_span_attributes(span, {"github.result": "not_found"})
                    set_span_ok(span)

                    if repository_is_optionally_missing:
                        logger.debug(
                            "Optional policy repository %s was not found",
                            repo_full_name,
                            extra={
                                "repo": repo_full_name,
                                "file_path": file_path,
                                "installation_id": installation_id,
                            },
                        )
                    else:
                        logger.debug(
                            "Policy file %s was not found in %s",
                            file_path,
                            repo_full_name,
                            extra={
                                "repo": repo_full_name,
                                "file_path": file_path,
                                "installation_id": installation_id,
                            },
                        )
                    return None

                github_api_requests_total.labels(operation="get_file", status="error").inc()
                add_span_attributes(span, {"github.result": "error"})
                set_span_error(span, e)
                logger.warning(
                    "GitHub could not read %s from %s (status %s)",
                    file_path,
                    repo_full_name,
                    e.status,
                    extra={
                        "repo": repo_full_name,
                        "file_path": file_path,
                        "installation_id": installation_id,
                    },
                )
                raise

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="get_file").observe(duration)
                github_api_requests_total.labels(operation="get_file", status="error").inc()
                add_span_attributes(span, {"github.result": "error"})
                set_span_error(span, e)
                logger.warning(
                    "GitHub could not read %s from %s (%s)",
                    file_path,
                    repo_full_name,
                    type(e).__name__,
                    extra={
                        "repo": repo_full_name,
                        "file_path": file_path,
                        "installation_id": installation_id,
                    },
                )
                raise

    def find_bot_approval_reviews(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
    ) -> list[BotReview] | None:
        """Find approval review states created by this bot on a PR.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            pr_number: Pull request number

        Returns:
            Review state details for bot approval reviews, or None when GitHub
            could not be read. Callers decide what an unknown state means for
            them; an empty list always means "no Stampbot approvals".
        """
        start_time = time.time()

        with create_span(
            "github.find_bot_approval_reviews",
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
                bot_user = self._bot_login()

                bot_reviews: list[BotReview] = []
                for review in pr.get_reviews():
                    if review.user.login == bot_user and review.state in (
                        "APPROVED",
                        "DISMISSED",
                    ):
                        bot_reviews.append(
                            {
                                "id": review.id,
                                "state": review.state,
                                "commit_id": getattr(review, "commit_id", None),
                            }
                        )

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="find_reviews").observe(
                    duration
                )
                github_api_requests_total.labels(operation="find_reviews", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(
                    span, {"github.reviews_found": len(bot_reviews), "github.bot_user": bot_user}
                )
                set_span_ok(span)

                return bot_reviews

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="find_reviews").observe(
                    duration
                )
                github_api_requests_total.labels(operation="find_reviews", status="failure").inc()

                set_span_error(span, e)

                logger.error(
                    "Failed to find bot approval reviews for PR #%s in %s: %s",
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
                return None

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

    def create_issue_comment(
        self,
        installation_id: int,
        repo_full_name: str,
        issue_number: int,
        message: str,
    ) -> bool:
        """Create a comment on an issue or pull request.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name (owner/repo)
            issue_number: Issue or pull request number
            message: Comment body

        Returns:
            True if successful, False otherwise
        """
        start_time = time.time()

        with create_span(
            "github.create_issue_comment",
            {
                "github.repo": repo_full_name,
                "github.issue_number": issue_number,
                "github.installation_id": installation_id,
            },
        ) as span:
            try:
                client = self._get_installation_client(installation_id)
                repo = client.get_repo(repo_full_name)
                issue = repo.get_issue(issue_number)

                issue.create_comment(message)

                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="issue_comment").observe(
                    duration
                )
                github_api_requests_total.labels(operation="issue_comment", status="success").inc()

                self._update_rate_limit_metrics(client, installation_id)

                add_span_attributes(span, {"github.result": "commented"})
                set_span_ok(span)

                logger.info(
                    "Posted issue comment on #%d in %s",
                    issue_number,
                    repo_full_name,
                    extra={
                        "repo": repo_full_name,
                        "issue_number": issue_number,
                        "installation_id": installation_id,
                    },
                )
                return True

            except Exception as e:
                duration = time.time() - start_time
                github_api_request_duration_seconds.labels(operation="issue_comment").observe(
                    duration
                )
                github_api_requests_total.labels(operation="issue_comment", status="failure").inc()

                set_span_error(span, e)

                logger.warning(
                    "Failed to post issue comment on #%d in %s: %s",
                    issue_number,
                    repo_full_name,
                    _sanitize_error(e),
                    extra={
                        "repo": repo_full_name,
                        "issue_number": issue_number,
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
                # The lazy client defers the request until the label is read.
                repo.get_label(label_name).complete()

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
                user = client.get_user(username)

                for team_ref in allowed_teams:
                    # Extract team slug (handle both "org/team" and "team" formats)
                    team_slug = team_ref.split("/")[-1] if "/" in team_ref else team_ref

                    try:
                        team = org.get_team_by_slug(team_slug)
                        # Check if user is a member
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
