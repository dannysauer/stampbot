# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""GitHub webhook event handling."""

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi.concurrency import run_in_threadpool
from opentelemetry.trace import Span

from stampbot.config import RepoConfig, repo_config_cache_seconds, settings
from stampbot.github_client import BotReview, _sanitize_error, github_client
from stampbot.logger import get_logger
from stampbot.metrics import (
    chatops_commands_total,
    errors_total,
    pr_approval_duration_seconds,
    pr_approvals_total,
    pr_dismissal_duration_seconds,
    pr_dismissals_total,
    webhook_events_total,
)
from stampbot.repo_policy import RepoPolicyResolver
from stampbot.telemetry import add_span_attributes, create_span, set_span_error, set_span_ok

logger = get_logger(__name__)

# Security: Maximum length for user-controlled strings to prevent DoS
MAX_COMMENT_LENGTH = 65536  # 64KB - generous but prevents abuse

# Pull request actions that can change Stampbot's review state. Every other
# action returns before any GitHub request is made.
PULL_REQUEST_ACTIONS = frozenset({"opened", "reopened", "labeled", "synchronize", "unlabeled"})

NO_ACTION = {"status": "ignored", "message": "No action needed"}


class Decision(Enum):
    """What a pull request event asks Stampbot to do."""

    APPROVE = "approve"
    REFRESH = "refresh"  # approve again only when a prior approval is stale
    DISMISS = "dismiss"
    NONE = "none"


@dataclass(frozen=True)
class PullRequestEvent:
    """The parts of a pull request webhook that policy decisions read."""

    action: str
    installation_id: int
    repo_full_name: str
    pr_number: int
    owner_login: str | None
    pr: dict[str, Any]
    event_label: str | None
    repo_config: RepoConfig

    @property
    def labels(self) -> list[str]:
        """Names of the labels currently on the pull request."""
        return [label["name"] for label in self.pr.get("labels", [])]

    @property
    def head_sha(self) -> str | None:
        """Current head commit, when GitHub included it."""
        sha = self.pr.get("head", {}).get("sha")
        return str(sha) if sha else None


class WebhookHandler:
    """Handle GitHub webhook events."""

    def __init__(self) -> None:
        """Initialize webhook handler (lazy initialization)."""
        self._webhook_secret: bytes | None = None
        self._policy = RepoPolicyResolver(repo_config_cache_seconds())

    @property
    def webhook_secret(self) -> bytes:
        """Get webhook secret, initializing if needed.

        Returns:
            Webhook secret as bytes.

        Raises:
            RuntimeError: If webhook secret is not configured.
        """
        if self._webhook_secret is None:
            if not settings.webhook_secret:
                raise RuntimeError(
                    "Webhook secret not configured. Visit /setup to create your GitHub App."
                )
            self._webhook_secret = settings.webhook_secret.encode()
        return self._webhook_secret

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify GitHub webhook signature.

        Args:
            payload: Request payload
            signature: X-Hub-Signature-256 header value

        Returns:
            True if signature is valid
        """
        if not signature:
            return False

        expected_signature = (
            "sha256=" + hmac.new(self.webhook_secret, payload, hashlib.sha256).hexdigest()
        )

        try:
            return hmac.compare_digest(expected_signature, signature)
        except TypeError:
            return False

    async def handle_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        delivery_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle webhook event.

        Args:
            event_type: GitHub event type (X-GitHub-Event header)
            payload: Event payload
            delivery_id: GitHub delivery GUID (X-GitHub-Delivery header), used to
                correlate traces with GitHub's delivery log

        Returns:
            Response dictionary
        """
        action = payload.get("action", "")

        # Track webhook event
        webhook_events_total.labels(event_type=event_type, action=action).inc()

        span_attributes: dict[str, Any] = {
            "webhook.event_type": event_type,
            "webhook.action": action,
        }
        if delivery_id:
            span_attributes["github.delivery_id"] = delivery_id

        with create_span("webhook.handle_event", span_attributes) as span:
            logger.info(
                "Received %s event",
                event_type,
                extra={"event_type": event_type, "action": action},
            )

            # Route to appropriate handler
            if event_type == "pull_request":
                result = await self._handle_pull_request(payload)
            elif event_type == "pull_request_review_comment":
                result = await self._handle_pr_comment(payload)
            elif event_type == "issue_comment":
                # Issue comments can be on PRs too
                if "pull_request" in payload.get("issue", {}):
                    result = await self._handle_pr_comment(payload)
                else:
                    result = {"status": "ignored", "message": "Not a PR comment"}
            elif event_type == "ping":
                result = {"status": "ok", "message": "pong"}
            else:
                result = {"status": "ignored", "message": f"Event type {event_type} not handled"}

            add_span_attributes(span, {"webhook.result_status": result.get("status", "unknown")})
            set_span_ok(span)

            return result

    async def _handle_pull_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle pull request events.

        Args:
            payload: Event payload

        Returns:
            Response dictionary
        """
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        repo = payload.get("repository", {})
        repo_full_name = repo.get("full_name")
        repo_default_branch = repo.get("default_branch") or "main"
        repo_owner = repo.get("owner", {})
        owner_login = repo_owner.get("login")
        owner_type = repo_owner.get("type")
        installation_id = payload.get("installation", {}).get("id")

        with create_span(
            "webhook.handle_pull_request",
            {
                "github.repo": repo_full_name or "unknown",
                "github.pr_number": pr_number or 0,
                "github.action": action or "unknown",
            },
        ) as span:
            if not all([pr_number, repo_full_name, installation_id]):
                logger.warning("Missing required fields in PR event")
                add_span_attributes(span, {"webhook.result": "missing_fields"})
                set_span_ok(span)
                return {"status": "error", "message": "Missing required fields"}

            if action not in PULL_REQUEST_ACTIONS:
                # Edits, review requests, closes, and similar actions never change
                # review state, so they must not cost a policy read.
                add_span_attributes(span, {"webhook.result": "action_ignored"})
                set_span_ok(span)
                return {"status": "ignored", "message": f"Action {action} not handled"}

            repo_config = await self._policy.get(
                installation_id,
                repo_full_name,
                repo_default_branch,
                owner_login,
                owner_type,
            )

            if repo_config.config_error:
                logger.warning(
                    "Invalid Stampbot configuration for %s: %s",
                    repo_full_name,
                    repo_config.config_error,
                    extra={"repo": repo_full_name, "error": repo_config.config_error},
                )
                add_span_attributes(
                    span,
                    {
                        "webhook.result": "config_error",
                        "webhook.config_error": repo_config.config_error,
                    },
                )

                if action == "opened":
                    await run_in_threadpool(
                        github_client.create_pr_review_comment,
                        installation_id,
                        repo_full_name,
                        pr_number,
                        (
                            "Stampbot configuration error:\n\n"
                            f"{repo_config.config_error}\n\n"
                            "Please fix the configuration to re-enable automation."
                        ),
                    )

                set_span_ok(span)
                return {
                    "status": "error",
                    "message": "Invalid repository configuration",
                }

            event = PullRequestEvent(
                action=action,
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                owner_login=owner_login,
                pr=pr,
                event_label=payload.get("label", {}).get("name"),
                repo_config=repo_config,
            )

            decision, label = self._decide(event)
            if decision is Decision.APPROVE and label:
                result = await self._approve_for_label(span, event, label)
            elif decision is Decision.REFRESH and label:
                result = await self._refresh_for_label(span, event, label)
            elif decision is Decision.DISMISS and label:
                result = await self._dismiss_for_label(span, event, label)
            else:
                add_span_attributes(span, {"webhook.result": "no_action"})
                set_span_ok(span)
                result = dict(NO_ACTION)

            if action == "opened":
                # This only produces an operator warning, so it runs after the
                # review decision instead of delaying it.
                await self._warn_missing_approval_labels(event)

            return result

    def _decide(self, event: PullRequestEvent) -> tuple[Decision, str | None]:
        """Map a pull request event to the action Stampbot takes.

        Args:
            event: Pull request event with valid repository policy.

        Returns:
            The decision and the approval label it concerns, if any.
        """
        config = event.repo_config
        if not config.auto_approve_on_label:
            return Decision.NONE, None

        if event.action == "unlabeled":
            if event.event_label in config.approval_labels:
                return Decision.DISMISS, event.event_label
            return Decision.NONE, None

        label = self._find_approval_label(event.labels, config)
        if label is None:
            return Decision.NONE, None

        if event.action in ("opened", "reopened"):
            # GitHub also sends a labeled event for every label present when a
            # pull request is created. Both events approve, because a missed
            # approval costs more than a duplicate one; _approve_pr dismisses
            # any duplicate afterwards.
            return Decision.APPROVE, label

        if event.action == "labeled":
            if event.event_label in config.approval_labels:
                return Decision.APPROVE, label
            return Decision.REFRESH, label

        if event.action == "synchronize" and config.reapprove:
            return Decision.REFRESH, label

        return Decision.NONE, None

    def _find_approval_label(self, labels: list[str], repo_config: RepoConfig) -> str | None:
        """Find the first configured approval label present on the PR.

        Args:
            labels: Current PR labels.
            repo_config: Repository configuration.

        Returns:
            Matching approval label, if present.
        """
        for label in repo_config.approval_labels:
            if label in labels:
                return label
        return None

    async def _refresh_for_label(
        self, span: Span | None, event: PullRequestEvent, label: str
    ) -> dict[str, Any]:
        """Approve again only when a prior Stampbot approval no longer covers the head.

        Args:
            span: Active tracing span (None if tracing disabled)
            event: Pull request event
            label: Approval label present on the pull request

        Returns:
            Response dictionary
        """
        reviews = await run_in_threadpool(
            github_client.find_bot_approval_reviews,
            event.installation_id,
            event.repo_full_name,
            event.pr_number,
        )
        if not self._approval_needs_refresh(reviews, event.head_sha):
            add_span_attributes(span, {"webhook.result": "no_action"})
            set_span_ok(span)
            return dict(NO_ACTION)
        return await self._approve_for_label(span, event, label, skip_existing_check=True)

    async def _approve_for_label(
        self,
        span: Span | None,
        event: PullRequestEvent,
        label: str,
        skip_existing_check: bool = False,
    ) -> dict[str, Any]:
        """Approve a pull request that carries an approval label, if it is eligible.

        Args:
            span: Active tracing span (None if tracing disabled)
            event: Pull request event
            label: Approval label that triggered the approval
            skip_existing_check: Whether the caller already checked for an active approval

        Returns:
            Response dictionary
        """
        config = event.repo_config
        pr_title = event.pr.get("title", "")
        pr_author = event.pr.get("user", {}).get("login", "")

        author_team_slugs: list[str] | None = None
        if config.needs_team_check(pr_author) and event.owner_login:
            author_team_slugs = await run_in_threadpool(
                github_client.get_user_team_slugs,
                event.installation_id,
                event.owner_login,
                pr_author,
                config.allowed_teams,
            )

        is_eligible, reason = await run_in_threadpool(
            config.is_pr_eligible,
            event.labels,
            pr_title,
            pr_author,
            author_team_slugs,
        )
        if not is_eligible:
            logger.info(
                "PR #%d not eligible for auto-approval: %s",
                event.pr_number,
                reason,
                extra={
                    "repo": event.repo_full_name,
                    "pr_number": event.pr_number,
                    "pr_author": pr_author,
                    "reason": reason,
                },
            )
            add_span_attributes(
                span,
                {"webhook.result": "not_eligible", "webhook.ineligible_reason": reason},
            )
            set_span_ok(span)
            return {"status": "ignored", "message": f"PR not eligible: {reason}"}

        logger.info(
            "PR #%d has approval label: %s",
            event.pr_number,
            label,
            extra={"repo": event.repo_full_name, "pr_number": event.pr_number, "label": label},
        )

        success = await self._approve_pr(
            event.installation_id,
            event.repo_full_name,
            event.pr_number,
            f"Auto-approved by Stampbot (label: {label})",
            "label",
            skip_existing_check=skip_existing_check,
            head_sha=event.head_sha,
        )

        add_span_attributes(
            span,
            {
                "webhook.result": "approved" if success else "approval_failed",
                "webhook.trigger_label": label,
            },
        )
        set_span_ok(span)

        return {
            "status": "success" if success else "error",
            "message": f"PR approved via label: {label}" if success else "Failed to approve PR",
        }

    async def _dismiss_for_label(
        self, span: Span | None, event: PullRequestEvent, label: str
    ) -> dict[str, Any]:
        """Dismiss Stampbot approvals after an approval label was removed.

        Args:
            span: Active tracing span (None if tracing disabled)
            event: Pull request event
            label: Approval label that was removed

        Returns:
            Response dictionary
        """
        logger.info(
            "Approval label %s removed from PR #%d",
            label,
            event.pr_number,
            extra={"repo": event.repo_full_name, "pr_number": event.pr_number, "label": label},
        )

        success = await self._dismiss_approvals(
            event.installation_id,
            event.repo_full_name,
            event.pr_number,
            f"Label {label} removed",
            "label_removed",
        )

        add_span_attributes(
            span,
            {
                "webhook.result": "dismissed" if success else "dismiss_failed",
                "webhook.removed_label": label,
            },
        )
        set_span_ok(span)

        return {
            "status": "success" if success else "error",
            "message": "Approvals dismissed" if success else "Failed to dismiss approvals",
        }

    async def _warn_missing_approval_labels(self, event: PullRequestEvent) -> None:
        """Warn when a configured approval label does not exist in the repository.

        Labels already present on the pull request exist by definition, so only
        the remaining configured labels are looked up.

        Args:
            event: Pull request event
        """
        present = event.labels
        for label in event.repo_config.approval_labels:
            if label in present:
                continue
            label_exists = await run_in_threadpool(
                github_client.repo_has_label,
                event.installation_id,
                event.repo_full_name,
                label,
            )
            if label_exists is False:
                logger.warning(
                    "Approval label %s not found in %s",
                    label,
                    event.repo_full_name,
                    extra={"repo": event.repo_full_name, "label": label},
                )

    def _approval_needs_refresh(
        self,
        reviews: list[BotReview],
        head_sha: str | None,
    ) -> bool:
        """Check whether prior Stampbot approval state should be refreshed.

        Args:
            reviews: Bot approval reviews.
            head_sha: Current PR head SHA.

        Returns:
            True when a prior approval exists but no active approval applies to the current head.
        """
        if not reviews:
            return False

        return not self._has_current_active_approval(reviews, head_sha)

    def _active_approvals_for_head(
        self,
        reviews: list[BotReview],
        head_sha: str | None,
        *,
        proven_only: bool = False,
    ) -> list[int]:
        """Return the IDs of Stampbot approvals that cover the current head, oldest first.

        By default an approval whose commit GitHub did not report, or a head
        Stampbot does not know, counts as covering the head: Stampbot never
        assumes an approval is stale without proof, so it never approves twice
        on a guess. ``proven_only`` inverts that bias for destructive use: only
        an approval whose commit is known to equal a known head qualifies, so
        nothing is dismissed on a guess either.

        Args:
            reviews: Bot review states.
            head_sha: Current PR head SHA.
            proven_only: Require a known commit that equals a known head.

        Returns:
            Sorted review IDs. GitHub assigns review IDs in creation order.
        """

        def covers_head(review: BotReview) -> bool:
            commit = review.get("commit_id")
            if proven_only:
                return bool(head_sha) and commit == head_sha
            return not head_sha or not commit or commit == head_sha

        return sorted(
            review["id"]
            for review in reviews
            if review["state"] == "APPROVED" and covers_head(review)
        )

    def _has_current_active_approval(
        self,
        reviews: list[BotReview],
        head_sha: str | None,
    ) -> bool:
        """Check whether Stampbot already approved the current pull request head.

        Args:
            reviews: Bot approval reviews.
            head_sha: Current PR head SHA.

        Returns:
            True when an active approval covers the current head.
        """
        return bool(self._active_approvals_for_head(reviews, head_sha))

    async def _handle_pr_comment(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle PR comment events for chatops.

        Args:
            payload: Event payload

        Returns:
            Response dictionary
        """
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")

        # Security: limit comment length to prevent DoS
        if len(comment_body) > MAX_COMMENT_LENGTH:
            return {"status": "ignored", "message": "Comment too long"}

        comment_body = comment_body.lower().strip()

        # Check if bot is mentioned
        if "@stampbot" not in comment_body:
            return {"status": "ignored", "message": "Bot not mentioned"}

        # Extract PR info
        if "pull_request" in payload.get("issue", {}):
            pr_url = payload["issue"]["pull_request"]["url"]
            # Extract PR number from URL
            pr_number = int(pr_url.split("/")[-1])
        elif "pull_request" in payload:
            pr_number = payload["pull_request"]["number"]
        else:
            return {"status": "error", "message": "Not a PR comment"}

        repo = payload.get("repository", {})
        repo_full_name = repo.get("full_name")
        repo_default_branch = repo.get("default_branch") or "main"
        repo_owner = repo.get("owner", {})
        owner_login = repo_owner.get("login")
        owner_type = repo_owner.get("type")
        installation_id = payload.get("installation", {}).get("id")
        commenter = comment.get("user", {}).get("login", "unknown")

        with create_span(
            "webhook.handle_chatops",
            {
                "github.repo": repo_full_name or "unknown",
                "github.pr_number": pr_number,
                "chatops.commenter": commenter,
            },
        ) as span:
            if not all([pr_number, repo_full_name, installation_id]):
                logger.warning("Missing required fields in comment event")
                add_span_attributes(span, {"chatops.result": "missing_fields"})
                set_span_ok(span)
                return {"status": "error", "message": "Missing required fields"}

            repo_config = await self._policy.get(
                installation_id,
                repo_full_name,
                repo_default_branch,
                owner_login,
                owner_type,
            )

            if repo_config.config_error:
                add_span_attributes(
                    span,
                    {
                        "chatops.result": "config_error",
                        "chatops.config_error": repo_config.config_error,
                    },
                )
                set_span_ok(span)
                return {
                    "status": "error",
                    "message": "Invalid repository configuration",
                }

            if not repo_config.chatops_enabled:
                add_span_attributes(span, {"chatops.result": "disabled"})
                set_span_ok(span)
                return {"status": "ignored", "message": "Chatops not enabled"}

            # Parse command
            command_match = re.search(r"@stampbot\s+(\w+)", comment_body)
            if not command_match:
                chatops_commands_total.labels(command="none", status="ignored").inc()
                add_span_attributes(span, {"chatops.result": "no_command"})
                set_span_ok(span)
                return {"status": "ignored", "message": "No command found"}

            command = command_match.group(1).lower()
            add_span_attributes(span, {"chatops.command": command})

            if command == "help":
                success = await self._post_help(
                    installation_id,
                    repo_full_name,
                    pr_number,
                    repo_config,
                )

                chatops_commands_total.labels(
                    command="help",
                    status="success" if success else "failure",
                ).inc()

                add_span_attributes(
                    span, {"chatops.result": "help_posted" if success else "help_failed"}
                )
                set_span_ok(span)

                return {
                    "status": "success" if success else "error",
                    "message": "Help message posted" if success else "Failed to post help message",
                }

            if command in repo_config.approve_commands + repo_config.unapprove_commands:
                has_permission = await run_in_threadpool(
                    github_client.user_has_permission,
                    installation_id,
                    repo_full_name,
                    commenter,
                    repo_config.chatops_required_permission,
                )
                if not has_permission:
                    chatops_commands_total.labels(command=command, status="forbidden").inc()
                    add_span_attributes(
                        span,
                        {
                            "chatops.result": "forbidden",
                            "chatops.required_permission": (
                                repo_config.chatops_required_permission
                            ),
                        },
                    )
                    set_span_ok(span)
                    return {
                        "status": "ignored",
                        "message": "Insufficient permissions for ChatOps commands",
                    }

            # Handle approve commands
            if command in repo_config.approve_commands:
                head_sha = await run_in_threadpool(
                    github_client.get_pr_head_sha,
                    installation_id,
                    repo_full_name,
                    pr_number,
                )
                success = await self._approve_pr(
                    installation_id,
                    repo_full_name,
                    pr_number,
                    f"Approved by @{commenter} via chatops",
                    "chatops",
                    head_sha=head_sha,
                )

                chatops_commands_total.labels(
                    command="approve",
                    status="success" if success else "failure",
                ).inc()

                add_span_attributes(
                    span, {"chatops.result": "approved" if success else "approval_failed"}
                )
                set_span_ok(span)

                return {
                    "status": "success" if success else "error",
                    "message": "PR approved" if success else "Failed to approve PR",
                }

            # Handle unapprove commands
            elif command in repo_config.unapprove_commands:
                success = await self._dismiss_approvals(
                    installation_id,
                    repo_full_name,
                    pr_number,
                    f"Unapproved by @{commenter} via chatops",
                    "chatops",
                )

                chatops_commands_total.labels(
                    command="unapprove",
                    status="success" if success else "failure",
                ).inc()

                add_span_attributes(
                    span, {"chatops.result": "unapproved" if success else "unapprove_failed"}
                )
                set_span_ok(span)

                return {
                    "status": "success" if success else "error",
                    "message": "Approvals dismissed" if success else "Failed to dismiss approvals",
                }

            chatops_commands_total.labels(command="unknown", status="ignored").inc()
            add_span_attributes(span, {"chatops.result": "unknown_command"})
            set_span_ok(span)
            return {"status": "ignored", "message": f"Unknown command: {command}"}

    def _format_help_message(self, repo_config: RepoConfig) -> str:
        """Format a help message for the effective repository configuration.

        Args:
            repo_config: Repository configuration

        Returns:
            Markdown help text.
        """
        lines = ["## Stampbot Help", ""]

        lines.extend(
            [
                "### ChatOps Commands",
                "",
                (
                    f"Approve and unapprove commands require "
                    f"**{repo_config.chatops_required_permission}** permission or higher."
                ),
                "",
            ]
        )

        approve_commands = ", ".join(
            f"`@stampbot {command}`" for command in repo_config.approve_commands
        )
        unapprove_commands = ", ".join(
            f"`@stampbot {command}`" for command in repo_config.unapprove_commands
        )
        lines.extend(
            [
                f"- **Approve**: {approve_commands}",
                f"- **Unapprove**: {unapprove_commands}",
                "- **Help**: `@stampbot help`",
                "",
                "### Label-Based Auto-Approval",
                "",
            ]
        )

        if repo_config.auto_approve_on_label:
            approval_labels = ", ".join(f"`{label}`" for label in repo_config.approval_labels)
            lines.append(f"Adding any of these labels can trigger approval: {approval_labels}")
        else:
            lines.append("Label-based auto-approval is disabled in this repository.")

        filters = []
        if repo_config.required_labels:
            filters.append(
                "required labels: "
                + ", ".join(f"`{label}`" for label in repo_config.required_labels)
            )
        if repo_config.required_title_patterns:
            filters.append(
                "required title patterns: "
                + ", ".join(f"`{pattern}`" for pattern in repo_config.required_title_patterns)
            )
        if repo_config.allowed_users:
            filters.append(
                "allowed users: " + ", ".join(f"`{user}`" for user in repo_config.allowed_users)
            )
        if repo_config.allowed_teams:
            filters.append(
                "allowed teams: " + ", ".join(f"`{team}`" for team in repo_config.allowed_teams)
            )

        if filters:
            lines.extend(["", "Additional approval filters apply:"])
            lines.extend(f"- {filter_text}" for filter_text in filters)

        return "\n".join(lines)

    async def _post_help(
        self,
        installation_id: int,
        repo_full_name: str,
        issue_number: int,
        repo_config: RepoConfig,
    ) -> bool:
        """Post contextual help to an issue or pull request.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            issue_number: Issue or pull request number
            repo_config: Repository configuration

        Returns:
            True if successful.
        """
        return await run_in_threadpool(
            github_client.create_issue_comment,
            installation_id,
            repo_full_name,
            issue_number,
            self._format_help_message(repo_config),
        )

    async def _approve_pr(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        comment: str,
        trigger_type: str,
        skip_existing_check: bool = False,
        head_sha: str | None = None,
    ) -> bool:
        """Approve a PR and track metrics.

        Checks for existing active approvals first to avoid duplicate reviews.
        Two replicas handling the ``opened`` and ``labeled`` events of one new
        pull request can both pass that check, so after a successful approval
        the reviews are read again and every Stampbot approval of the same head
        except the oldest is dismissed. Approving twice and dismissing one is
        preferred to deferring, which could miss an approval when the other
        event never arrives.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            pr_number: PR number
            comment: Approval comment
            trigger_type: What triggered the approval (label, chatops)
            skip_existing_check: Whether to skip duplicate active approval detection
            head_sha: Current PR head SHA, used to distinguish stale approvals

        Returns:
            True if successful (or already approved)
        """
        with create_span(
            "webhook.approve_pr",
            {
                "github.repo": repo_full_name,
                "github.pr_number": pr_number,
                "approval.trigger_type": trigger_type,
            },
        ) as span:
            if not skip_existing_check:
                reviews = await run_in_threadpool(
                    github_client.find_bot_approval_reviews,
                    installation_id,
                    repo_full_name,
                    pr_number,
                )
                existing_approvals = self._active_approvals_for_head(reviews, head_sha)
                if existing_approvals:
                    logger.info(
                        "PR #%d in %s already has active approval for current head, skipping",
                        pr_number,
                        repo_full_name,
                        extra={
                            "repo": repo_full_name,
                            "pr_number": pr_number,
                            "existing_review_ids": existing_approvals,
                        },
                    )
                    add_span_attributes(
                        span,
                        {
                            "approval.result": "already_approved",
                            "approval.existing_reviews": len(existing_approvals),
                        },
                    )
                    set_span_ok(span)
                    return True

            start_time = time.time()

            success = await run_in_threadpool(
                github_client.approve_pr,
                installation_id,
                repo_full_name,
                pr_number,
                comment,
            )

            duration = time.time() - start_time
            pr_approval_duration_seconds.observe(duration)

            status = "success" if success else "failure"
            pr_approvals_total.labels(
                trigger_type=trigger_type,
                status=status,
            ).inc()

            if not success:
                errors_total.labels(error_type="approval_failed").inc()
            else:
                # Another replica may have approved the same head in the same
                # second. Keep the oldest approval; the outcome of this cleanup
                # never changes the result of the approval that was posted.
                await self._dismiss_approvals(
                    installation_id,
                    repo_full_name,
                    pr_number,
                    "Duplicate Stampbot approval",
                    "duplicate",
                    head_sha=head_sha,
                    keep_oldest=True,
                )

            add_span_attributes(span, {"approval.result": status})
            set_span_ok(span)

            return success

    async def _dismiss_approvals(
        self,
        installation_id: int,
        repo_full_name: str,
        pr_number: int,
        message: str,
        trigger_type: str,
        *,
        head_sha: str | None = None,
        keep_oldest: bool = False,
    ) -> bool:
        """Dismiss active Stampbot approvals on a PR.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            pr_number: PR number
            message: Dismissal message
            trigger_type: What triggered the dismissal (label_removed, chatops, duplicate)
            head_sha: Only approvals covering this head are dismissed. None means every
                active approval.
            keep_oldest: Leave the oldest matching approval in place, which turns the
                call into duplicate cleanup.

        Returns:
            True if every selected approval was dismissed, or none needed dismissal.
        """
        with create_span(
            "webhook.dismiss_approvals",
            {
                "github.repo": repo_full_name,
                "github.pr_number": pr_number,
                "dismissal.trigger_type": trigger_type,
            },
        ) as span:
            start_time = time.time()

            try:
                reviews = await run_in_threadpool(
                    github_client.find_bot_approval_reviews,
                    installation_id,
                    repo_full_name,
                    pr_number,
                )
                if keep_oldest:
                    # Duplicate cleanup only touches approvals proven to be of
                    # this head, and leaves the oldest of them in place.
                    review_ids = self._active_approvals_for_head(
                        reviews, head_sha, proven_only=True
                    )[1:]
                else:
                    review_ids = self._active_approvals_for_head(reviews, head_sha)

                add_span_attributes(span, {"dismissal.reviews_found": len(review_ids)})

                if not review_ids:
                    logger.debug(
                        "No Stampbot approvals to dismiss on PR #%d",
                        pr_number,
                        extra={"repo": repo_full_name, "pr_number": pr_number},
                    )
                    pr_dismissal_duration_seconds.observe(time.time() - start_time)
                    add_span_attributes(span, {"dismissal.result": "no_reviews"})
                    set_span_ok(span)
                    return True

                # Dismiss each review
                success = True
                for review_id in review_ids:
                    result = await run_in_threadpool(
                        github_client.dismiss_approval,
                        installation_id,
                        repo_full_name,
                        pr_number,
                        review_id,
                        message,
                    )
                    success = success and result

                duration = time.time() - start_time
                pr_dismissal_duration_seconds.observe(duration)

                status = "success" if success else "failure"
                pr_dismissals_total.labels(trigger_type=trigger_type, status=status).inc()

                add_span_attributes(span, {"dismissal.result": status})
                set_span_ok(span)

                return success

            except Exception as e:
                duration = time.time() - start_time
                pr_dismissal_duration_seconds.observe(duration)
                pr_dismissals_total.labels(trigger_type=trigger_type, status="failure").inc()

                logger.error(
                    "Error dismissing approvals: %s",
                    _sanitize_error(e),
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "error": _sanitize_error(e),
                    },
                )
                errors_total.labels(error_type="dismiss_failed").inc()

                set_span_error(span, e)
                return False


# Global handler instance
webhook_handler = WebhookHandler()
