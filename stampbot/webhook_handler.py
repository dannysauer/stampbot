# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""GitHub webhook event handling."""

import hashlib
import hmac
import re
import time
from typing import Any

from fastapi.concurrency import run_in_threadpool

from stampbot.config import RepoConfig, settings
from stampbot.github_client import github_client
from stampbot.logger import get_logger
from stampbot.metrics import (
    chatops_commands_total,
    errors_total,
    pr_approval_duration_seconds,
    pr_approvals_total,
    pr_dismissal_duration_seconds,
    pr_dismissals_total,
    repo_config_loads_total,
    webhook_events_total,
)
from stampbot.telemetry import add_span_attributes, create_span, set_span_error, set_span_ok

logger = get_logger(__name__)

# Security: Maximum length for user-controlled strings to prevent DoS
MAX_COMMENT_LENGTH = 65536  # 64KB - generous but prevents abuse


class WebhookHandler:
    """Handle GitHub webhook events."""

    def __init__(self) -> None:
        """Initialize webhook handler (lazy initialization)."""
        self._webhook_secret: bytes | None = None

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
    ) -> dict[str, Any]:
        """Handle webhook event.

        Args:
            event_type: GitHub event type (X-GitHub-Event header)
            payload: Event payload

        Returns:
            Response dictionary
        """
        action = payload.get("action", "")

        # Track webhook event
        webhook_events_total.labels(event_type=event_type, action=action).inc()

        with create_span(
            "webhook.handle_event",
            {
                "webhook.event_type": event_type,
                "webhook.action": action,
            },
        ) as span:
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

            # Get repository configuration
            repo_config = await self._get_repo_config(
                installation_id,
                repo_full_name,
                repo_default_branch,
                owner_login,
                owner_type,
            )

            if repo_config.config_error:
                logger.warning(
                    "Invalid stampbot.toml in %s: %s",
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
                            "Stampbot configuration error in `stampbot.toml`:\n\n"
                            f"{repo_config.config_error}\n\n"
                            "Please fix the file to re-enable automation."
                        ),
                    )

                set_span_ok(span)
                return {
                    "status": "error",
                    "message": "Invalid repository configuration",
                }

            if action == "opened":
                for label in repo_config.approval_labels:
                    label_exists = await run_in_threadpool(
                        github_client.repo_has_label,
                        installation_id,
                        repo_full_name,
                        label,
                    )
                    if label_exists is False:
                        logger.warning(
                            "Approval label %s not found in %s",
                            label,
                            repo_full_name,
                            extra={"repo": repo_full_name, "label": label},
                        )

            # Check if we should approve based on labels
            if repo_config.auto_approve_on_label and action in [
                "opened",
                "reopened",
                "labeled",
                "synchronize",
            ]:
                labels = [label["name"] for label in pr.get("labels", [])]
                pr_title = pr.get("title", "")
                pr_author = pr.get("user", {}).get("login", "")

                for label in labels:
                    if label in repo_config.approval_labels:
                        # Check if team membership verification is needed
                        author_team_slugs: list[str] | None = None
                        if repo_config.needs_team_check(pr_author) and owner_login:
                            author_team_slugs = await run_in_threadpool(
                                github_client.get_user_team_slugs,
                                installation_id,
                                owner_login,
                                pr_author,
                                repo_config.allowed_teams,
                            )

                        # Check if PR passes eligibility filters
                        is_eligible, reason = repo_config.is_pr_eligible(
                            labels, pr_title, pr_author, author_team_slugs
                        )
                        if not is_eligible:
                            logger.info(
                                "PR #%d not eligible for auto-approval: %s",
                                pr_number,
                                reason,
                                extra={
                                    "repo": repo_full_name,
                                    "pr_number": pr_number,
                                    "pr_author": pr_author,
                                    "reason": reason,
                                },
                            )
                            add_span_attributes(
                                span,
                                {
                                    "webhook.result": "not_eligible",
                                    "webhook.ineligible_reason": reason,
                                },
                            )
                            set_span_ok(span)
                            return {
                                "status": "ignored",
                                "message": f"PR not eligible: {reason}",
                            }

                        logger.info(
                            "PR #%d has approval label: %s",
                            pr_number,
                            label,
                            extra={
                                "repo": repo_full_name,
                                "pr_number": pr_number,
                                "label": label,
                            },
                        )

                        # Approve the PR
                        success = await self._approve_pr(
                            installation_id,
                            repo_full_name,
                            pr_number,
                            f"Auto-approved by Stampbot (label: {label})",
                            "label",
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
                            "message": (
                                f"PR approved via label: {label}"
                                if success
                                else "Failed to approve PR"
                            ),
                        }

            # Check if we should remove approval when label is removed
            if repo_config.auto_approve_on_label and action == "unlabeled":
                removed_label = payload.get("label", {}).get("name")
                if removed_label in repo_config.approval_labels:
                    logger.info(
                        "Approval label %s removed from PR #%d",
                        removed_label,
                        pr_number,
                        extra={
                            "repo": repo_full_name,
                            "pr_number": pr_number,
                            "label": removed_label,
                        },
                    )

                    # Dismiss bot approvals
                    success = await self._dismiss_approvals(
                        installation_id,
                        repo_full_name,
                        pr_number,
                        f"Label {removed_label} removed",
                        "label_removed",
                    )

                    add_span_attributes(
                        span,
                        {
                            "webhook.result": "dismissed" if success else "dismiss_failed",
                            "webhook.removed_label": removed_label,
                        },
                    )
                    set_span_ok(span)

                    return {
                        "status": "success" if success else "error",
                        "message": (
                            "Approvals dismissed" if success else "Failed to dismiss approvals"
                        ),
                    }

            add_span_attributes(span, {"webhook.result": "no_action"})
            set_span_ok(span)
            return {"status": "ignored", "message": "No action needed"}

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

            # Get repository configuration
            repo_config = await self._get_repo_config(
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
                success = await self._approve_pr(
                    installation_id,
                    repo_full_name,
                    pr_number,
                    f"Approved by @{commenter} via chatops",
                    "chatops",
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

    async def _get_repo_config(
        self,
        installation_id: int,
        repo_full_name: str,
        default_branch: str,
        owner_login: str | None,
        owner_type: str | None,
    ) -> RepoConfig:
        """Get repository configuration from stampbot.toml.

        Reads from the repo's default branch first, then falls back to the
        organization-wide .github repo if available.

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
                    "stampbot.toml",
                    default_branch,
                )

                if content:
                    return self._parse_repo_config(span, content, source_repo=repo_full_name)

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
                        "stampbot.toml",
                        None,
                    )
                    if org_content:
                        return self._parse_repo_config(
                            span, org_content, source_repo=org_repo_full_name
                        )

                repo_config_loads_total.labels(status="default").inc()
                logger.info(
                    "No stampbot.toml found in %s%s, using defaults",
                    repo_full_name,
                    f" or {org_repo_full_name}" if org_repo_full_name else "",
                    extra={"repo": repo_full_name, "org_repo": org_repo_full_name},
                )
                add_span_attributes(span, {"config.result": "default"})
                set_span_ok(span)
                return RepoConfig.default()

            except Exception as e:
                repo_config_loads_total.labels(status="error").inc()
                logger.warning(
                    "Error loading config from %s: %s, using defaults",
                    repo_full_name,
                    e,
                    extra={"repo": repo_full_name, "error": str(e)},
                )
                add_span_attributes(span, {"config.result": "error"})
                set_span_error(span, e)
                return RepoConfig.default()

    def _parse_repo_config(
        self,
        span: Any,
        toml_content: str,
        source_repo: str,
    ) -> RepoConfig:
        """Parse repository config content with metrics and tracing.

        Args:
            span: Active tracing span (can be None if tracing disabled)
            toml_content: Raw TOML content
            source_repo: Repository name where config was loaded

        Returns:
            RepoConfig instance (default with config_error on invalid config)
        """
        try:
            repo_config = RepoConfig.from_toml(toml_content)
        except ValueError as e:
            repo_config_loads_total.labels(status="error").inc()
            add_span_attributes(
                span,
                {
                    "config.result": "error",
                    "config.error": str(e),
                    "config.source_repo": source_repo,
                },
            )
            set_span_error(span, e)
            return RepoConfig.default().with_config_error(str(e))

        repo_config_loads_total.labels(status="found").inc()
        add_span_attributes(
            span,
            {"config.result": "found", "config.source_repo": source_repo},
        )
        set_span_ok(span)
        return repo_config

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
    ) -> bool:
        """Approve a PR and track metrics.

        Checks for existing active approvals first to avoid duplicate comments.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            pr_number: PR number
            comment: Approval comment
            trigger_type: What triggered the approval (label, chatops)

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
            # Check for existing active approval to avoid duplicates
            existing_approvals = await run_in_threadpool(
                github_client.find_bot_reviews,
                installation_id,
                repo_full_name,
                pr_number,
            )

            if existing_approvals:
                logger.info(
                    "PR #%d in %s already has active approval, skipping",
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
    ) -> bool:
        """Dismiss bot approvals on a PR.

        Args:
            installation_id: GitHub App installation ID
            repo_full_name: Repository full name
            pr_number: PR number
            message: Dismissal message
            trigger_type: What triggered the dismissal (label_removed, chatops)

        Returns:
            True if successful
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
                # Find all bot reviews
                review_ids = await run_in_threadpool(
                    github_client.find_bot_reviews,
                    installation_id,
                    repo_full_name,
                    pr_number,
                )

                add_span_attributes(span, {"dismissal.reviews_found": len(review_ids)})

                if not review_ids:
                    logger.info(
                        "No bot approvals found on PR #%d",
                        pr_number,
                        extra={"repo": repo_full_name, "pr_number": pr_number},
                    )
                    duration = time.time() - start_time
                    pr_dismissal_duration_seconds.observe(duration)
                    pr_dismissals_total.labels(trigger_type=trigger_type, status="success").inc()
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
                    e,
                    extra={
                        "repo": repo_full_name,
                        "pr_number": pr_number,
                        "error": str(e),
                    },
                )
                errors_total.labels(error_type="dismiss_failed").inc()

                set_span_error(span, e)
                return False


# Global handler instance
webhook_handler = WebhookHandler()
