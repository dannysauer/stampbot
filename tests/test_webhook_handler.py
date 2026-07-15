"""Tests for webhook handler."""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from tests.conftest import load_fixture


@pytest.fixture
def mock_github_client():
    """Create a mock GitHub client."""
    with patch("stampbot.webhook_handler.github_client") as mock:
        # Default successful responses
        mock.get_repo_file.return_value = None  # No config file, use defaults
        mock.approve_pr.return_value = True
        mock.create_pr_review_comment.return_value = True
        mock.create_issue_comment.return_value = True
        mock.dismiss_approval.return_value = True
        mock.find_bot_reviews.return_value = []
        mock.find_bot_approval_reviews.return_value = []
        mock.get_pr_head_sha.return_value = "current-head"
        mock.repo_has_label.return_value = True
        mock.user_has_permission.return_value = True
        mock.get_user_team_slugs.return_value = []  # No team memberships by default
        yield mock


@pytest.fixture
def webhook_handler():
    """Create webhook handler instance."""
    from stampbot.webhook_handler import WebhookHandler

    return WebhookHandler()


# =============================================================================
# Ping Event Tests
# =============================================================================


@pytest.mark.asyncio
async def test_ping_event(webhook_handler):
    """Test handling ping event."""
    payload = {"zen": "Design for failure."}
    result = await webhook_handler.handle_event("ping", payload)
    assert result["status"] == "ok"
    assert result["message"] == "pong"


@pytest.mark.asyncio
async def test_unknown_event(webhook_handler):
    """Test handling unknown event type."""
    payload = {}
    result = await webhook_handler.handle_event("unknown", payload)
    assert result["status"] == "ignored"


# =============================================================================
# Pull Request Event Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pr_opened_with_autoapprove_label(webhook_handler, mock_github_client):
    """Test PR opened with autoapprove label triggers approval."""
    payload = load_fixture("pr_opened_with_autoapprove_label")

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    assert "approved" in result["message"].lower()
    mock_github_client.approve_pr.assert_called_once_with(
        12345,  # installation_id
        "octocat/hello-world",  # repo
        42,  # pr_number
        "Auto-approved by Stampbot (label: autoapprove)",
    )


@pytest.mark.asyncio
async def test_pr_opened_no_labels(webhook_handler, mock_github_client):
    """Test PR opened without autoapprove label is ignored."""
    payload = load_fixture("pr_opened_no_labels")

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "no action" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_opened_missing_label_logs_warning(webhook_handler, mock_github_client):
    """Test missing approval label triggers label check."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.repo_has_label.return_value = False

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    assert mock_github_client.repo_has_label.call_count == 2


@pytest.mark.asyncio
async def test_pr_label_ignored_when_auto_approve_disabled(webhook_handler, mock_github_client):
    """Test label approvals are ignored when auto_approve_on_label is disabled."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.return_value = "auto_approve_on_label = false"

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_labeled_autoapprove(webhook_handler, mock_github_client):
    """Test adding autoapprove label triggers approval."""
    payload = load_fixture("pr_labeled_autoapprove")

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    assert "approved" in result["message"].lower()
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_pr_labeled_unrelated_label_with_approval_label_ignored(
    webhook_handler, mock_github_client
):
    """Test unrelated label changes do not re-approve just because an approval label exists."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["label"] = {"name": "bug"}
    payload["pull_request"]["labels"].append({"name": "bug"})

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "no action" in result["message"].lower()
    mock_github_client.find_bot_approval_reviews.assert_called_once()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_labeled_unrelated_label_reapproves_dismissed_review(
    webhook_handler, mock_github_client
):
    """Test unrelated label changes re-approve when prior Stampbot approval was dismissed."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["label"] = {"name": "bug"}
    payload["pull_request"]["labels"].append({"name": "bug"})
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 123, "state": "DISMISSED", "commit_id": "abc123def456"}
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()
    mock_github_client.find_bot_reviews.assert_not_called()


@pytest.mark.asyncio
async def test_pr_synchronize_reapprove_disabled(webhook_handler, mock_github_client):
    """Test new commits do not re-approve by default."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["action"] = "synchronize"
    payload.pop("label", None)

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "no action" in result["message"].lower()
    mock_github_client.find_bot_approval_reviews.assert_not_called()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_synchronize_reapprove_enabled_for_stale_review(
    webhook_handler, mock_github_client
):
    """Test new commits re-approve when reapprove is enabled and prior approval is stale."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["action"] = "synchronize"
    payload.pop("label", None)
    mock_github_client.get_repo_file.return_value = "reapprove = true"
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 123, "state": "APPROVED", "commit_id": "oldsha"}
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()
    mock_github_client.find_bot_reviews.assert_not_called()


@pytest.mark.asyncio
async def test_pr_synchronize_reapprove_enabled_current_review_ignored(
    webhook_handler, mock_github_client
):
    """Test new commits do not re-approve when current head already has approval."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["action"] = "synchronize"
    payload.pop("label", None)
    mock_github_client.get_repo_file.return_value = "reapprove = true"
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 123, "state": "APPROVED", "commit_id": "abc123def456"}
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_should_approve_for_unsupported_pr_event(webhook_handler):
    """Test approval decision helper rejects unsupported PR actions."""
    from stampbot.config import RepoConfig

    should_approve, skip_existing_check = await webhook_handler._should_approve_for_pr_event(
        "closed",
        {},
        "current-head",
        12345,
        "octocat/hello-world",
        42,
        RepoConfig.default(),
    )

    assert should_approve is False
    assert skip_existing_check is False


@pytest.mark.asyncio
async def test_pr_labeled_skips_duplicate_approval(webhook_handler, mock_github_client):
    """Test that existing approval prevents duplicate approval comment."""
    payload = load_fixture("pr_labeled_autoapprove")
    # Simulate existing active approval
    mock_github_client.find_bot_reviews.return_value = [12345]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    # find_bot_reviews should be called to check for existing approval
    mock_github_client.find_bot_reviews.assert_called_once()
    # approve_pr should NOT be called since there's already an approval
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_unlabeled_autoapprove(webhook_handler, mock_github_client):
    """Test removing autoapprove label dismisses approvals."""
    payload = load_fixture("pr_unlabeled_autoapprove")
    mock_github_client.find_bot_reviews.return_value = [111, 222]  # Mock review IDs

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    assert "dismissed" in result["message"].lower()
    mock_github_client.find_bot_reviews.assert_called_once()
    assert mock_github_client.dismiss_approval.call_count == 2


@pytest.mark.asyncio
async def test_pr_unlabeled_ignored_when_auto_approve_disabled(webhook_handler, mock_github_client):
    """Test label removal is ignored when auto_approve_on_label is disabled."""
    payload = load_fixture("pr_unlabeled_autoapprove")
    mock_github_client.get_repo_file.return_value = "auto_approve_on_label = false"

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    mock_github_client.find_bot_reviews.assert_not_called()
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.asyncio
async def test_pr_unlabeled_no_bot_reviews(webhook_handler, mock_github_client):
    """Test removing label when no bot reviews exist."""
    payload = load_fixture("pr_unlabeled_autoapprove")
    mock_github_client.find_bot_reviews.return_value = []  # No reviews to dismiss

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.asyncio
async def test_pr_approval_failure(webhook_handler, mock_github_client):
    """Test handling approval failure."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.approve_pr.return_value = False  # Simulate failure

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"
    assert "failed" in result["message"].lower()


@pytest.mark.asyncio
async def test_pr_missing_installation_id(webhook_handler, mock_github_client):
    """Test PR event with missing installation ID."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    del payload["installation"]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"
    assert "missing" in result["message"].lower()


@pytest.mark.asyncio
async def test_pr_not_eligible_missing_required_label(webhook_handler, mock_github_client):
    """Test PR with approval label but missing required label is not approved."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.return_value = 'required_labels = ["dependencies"]'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "not eligible" in result["message"].lower()
    assert "missing required label" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_not_eligible_title_pattern_no_match(webhook_handler, mock_github_client):
    """Test PR with approval label but title doesn't match pattern is not approved."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.return_value = 'required_title_patterns = ["^chore:"]'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "not eligible" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pathological_pr_title_pattern_is_bounded(webhook_handler, mock_github_client):
    """Test a pathological title pattern fails closed within a bounded time."""
    from stampbot.config import MAX_PR_TITLE_LENGTH

    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["pull_request"]["title"] = "a" * (MAX_PR_TITLE_LENGTH - 1) + "!"
    mock_github_client.get_repo_file.return_value = 'required_title_patterns = ["(a|aa)+$"]'

    result = await asyncio.wait_for(
        webhook_handler.handle_event("pull_request", payload), timeout=1.0
    )

    assert result["status"] == "ignored"
    assert "safety limit" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_eligibility_does_not_block_event_loop(
    webhook_handler, mock_github_client, monkeypatch
):
    """Test title eligibility work runs outside the asyncio event loop."""
    from stampbot.config import RepoConfig

    payload = load_fixture("pr_opened_with_autoapprove_label")
    eligibility_started = threading.Event()

    def slow_eligibility(*_args, **_kwargs):
        eligibility_started.set()
        time.sleep(0.3)
        return False, "test rejection"

    monkeypatch.setattr(RepoConfig, "is_pr_eligible", slow_eligibility)
    task = asyncio.create_task(webhook_handler.handle_event("pull_request", payload))

    start = time.monotonic()
    started = await asyncio.wait_for(asyncio.to_thread(eligibility_started.wait, 0.5), timeout=0.6)
    assert started is True
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.05)
    assert time.monotonic() - start < 0.15

    result = await task
    assert result["status"] == "ignored"
    assert "test rejection" in result["message"]


@pytest.mark.asyncio
async def test_pr_eligible_with_required_label(webhook_handler, mock_github_client):
    """Test PR with approval label and matching required label is approved."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    # The fixture has "autoapprove" label, so require it
    mock_github_client.get_repo_file.return_value = 'required_labels = ["autoapprove"]'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()


# =============================================================================
# Issue Comment (Chatops) Tests
# =============================================================================


@pytest.mark.asyncio
async def test_issue_comment_approve(webhook_handler, mock_github_client):
    """Test @stampbot approve command."""
    payload = load_fixture("issue_comment_approve")

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    assert "approved" in result["message"].lower()
    mock_github_client.approve_pr.assert_called_once()
    mock_github_client.user_has_permission.assert_called_once()


@pytest.mark.asyncio
async def test_issue_comment_approve_skips_duplicate(webhook_handler, mock_github_client):
    """Test chatops approve skips duplicate when PR already approved."""
    payload = load_fixture("issue_comment_approve")
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 12345, "state": "APPROVED", "commit_id": "current-head"}
    ]

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    mock_github_client.get_pr_head_sha.assert_called_once()
    mock_github_client.find_bot_approval_reviews.assert_called_once()
    mock_github_client.find_bot_reviews.assert_not_called()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_issue_comment_approve_refreshes_stale_approval(webhook_handler, mock_github_client):
    """Test chatops approve creates a new approval when the old approval is stale."""
    payload = load_fixture("issue_comment_approve")
    mock_github_client.get_pr_head_sha.return_value = "new-head"
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 12345, "state": "APPROVED", "commit_id": "old-head"}
    ]

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    mock_github_client.get_pr_head_sha.assert_called_once()
    mock_github_client.find_bot_approval_reviews.assert_called_once()
    mock_github_client.find_bot_reviews.assert_not_called()
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_issue_comment_unapprove(webhook_handler, mock_github_client):
    """Test @stampbot unapprove command."""
    payload = load_fixture("issue_comment_unapprove")
    mock_github_client.find_bot_reviews.return_value = [111]

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    assert "dismissed" in result["message"].lower()
    mock_github_client.user_has_permission.assert_called_once()


@pytest.mark.asyncio
async def test_issue_comment_no_bot_mention(webhook_handler, mock_github_client):
    """Test comment without bot mention is ignored."""
    payload = load_fixture("issue_comment_no_bot_mention")

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "not mentioned" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_issue_comment_too_long_rejected(webhook_handler, mock_github_client):
    """Test that comments exceeding max length are rejected to prevent DoS."""
    payload = load_fixture("issue_comment_approve")
    # Create an excessively long comment (> 64KB)
    payload["comment"]["body"] = "@stampbot approve " + "x" * 100000

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "too long" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_issue_comment_unknown_command(webhook_handler, mock_github_client):
    """Test unknown chatops command is ignored."""
    payload = load_fixture("issue_comment_approve")
    payload["comment"]["body"] = "@stampbot unknown_command"

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "unknown command" in result["message"].lower()
    mock_github_client.user_has_permission.assert_not_called()
    mock_github_client.create_issue_comment.assert_not_called()


@pytest.mark.asyncio
async def test_issue_comment_help_command(webhook_handler, mock_github_client):
    """Test @stampbot help posts contextual help."""
    payload = load_fixture("issue_comment_approve")
    payload["comment"]["body"] = "@stampbot help"

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    assert "help" in result["message"].lower()
    mock_github_client.user_has_permission.assert_not_called()
    mock_github_client.create_issue_comment.assert_called_once()
    comment_args = mock_github_client.create_issue_comment.call_args[0]
    assert comment_args[:3] == (12345, "octocat/hello-world", 42)
    assert "@stampbot approve" in comment_args[3]
    assert "@stampbot unapprove" in comment_args[3]
    assert "`autoapprove`" in comment_args[3]


@pytest.mark.asyncio
async def test_issue_comment_help_uses_custom_repo_config(webhook_handler, mock_github_client):
    """Test help reflects custom repo commands, labels, and filters."""
    payload = load_fixture("issue_comment_approve")
    payload["comment"]["body"] = "@stampbot help"
    mock_github_client.get_repo_file.return_value = """
approval_labels = ["ship-it"]
approve_commands = ["approve-it"]
unapprove_commands = ["hold-it"]
required_labels = ["dependencies"]
required_title_patterns = ["^chore:"]
allowed_users = ["renovate[bot]"]
allowed_teams = ["release-team"]
"""

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    help_text = mock_github_client.create_issue_comment.call_args[0][3]
    assert "@stampbot approve-it" in help_text
    assert "@stampbot hold-it" in help_text
    assert "`ship-it`" in help_text
    assert "`dependencies`" in help_text
    assert "`^chore:`" in help_text
    assert "`renovate[bot]`" in help_text
    assert "`release-team`" in help_text


@pytest.mark.asyncio
async def test_issue_comment_help_shows_label_approval_disabled(
    webhook_handler, mock_github_client
):
    """Test help indicates when label-based approval is disabled."""
    payload = load_fixture("issue_comment_approve")
    payload["comment"]["body"] = "@stampbot help"
    mock_github_client.get_repo_file.return_value = "auto_approve_on_label = false"

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    help_text = mock_github_client.create_issue_comment.call_args[0][3]
    assert "Label-based auto-approval is disabled" in help_text


@pytest.mark.asyncio
async def test_issue_comment_help_disabled_with_chatops(webhook_handler, mock_github_client):
    """Test help is ignored when chatops is disabled."""
    payload = load_fixture("issue_comment_approve")
    payload["comment"]["body"] = "@stampbot help"
    mock_github_client.get_repo_file.return_value = "chatops_enabled = false"

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "not enabled" in result["message"].lower()
    mock_github_client.create_issue_comment.assert_not_called()


@pytest.mark.asyncio
async def test_issue_comment_not_on_pr(webhook_handler, mock_github_client):
    """Test comment on regular issue (not PR) is ignored."""
    payload = load_fixture("issue_comment_approve")
    del payload["issue"]["pull_request"]  # Remove PR reference

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"


@pytest.mark.asyncio
async def test_issue_comment_stamp_command(webhook_handler, mock_github_client):
    """Test @stampbot stamp command (alias for approve)."""
    payload = load_fixture("issue_comment_approve")
    payload["comment"]["body"] = "@stampbot stamp"

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    mock_github_client.user_has_permission.assert_called_once()


@pytest.mark.asyncio
async def test_issue_comment_approve_forbidden(webhook_handler, mock_github_client):
    """Test @stampbot approve command requires write access."""
    payload = load_fixture("issue_comment_approve")
    mock_github_client.user_has_permission.return_value = False

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "insufficient permissions" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()
    mock_github_client.user_has_permission.assert_called_once()


@pytest.mark.asyncio
async def test_chatops_does_not_block_event_loop(webhook_handler, mock_github_client):
    """Test chatops handlers offload blocking GitHub calls."""
    payload = load_fixture("issue_comment_approve")

    def slow_permission(*_args, **_kwargs):
        time.sleep(0.2)
        return True

    def slow_approve(*_args, **_kwargs):
        time.sleep(0.2)
        return True

    mock_github_client.user_has_permission.side_effect = slow_permission
    mock_github_client.approve_pr.side_effect = slow_approve

    task = asyncio.create_task(webhook_handler.handle_event("issue_comment", payload))

    start = time.monotonic()
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.05)
    assert time.monotonic() - start < 0.1

    result = await task
    assert result["status"] == "success"


# =============================================================================
# Repo Config Tests
# =============================================================================


@pytest.mark.asyncio
async def test_custom_repo_config(webhook_handler, mock_github_client):
    """Test loading custom repo config from stampbot.toml."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    # Change label to custom one
    payload["pull_request"]["labels"] = [{"name": "custom-approve"}]

    # Mock custom config
    mock_github_client.get_repo_file.return_value = """
approval_labels = ["custom-approve"]
auto_approve_on_label = true
chatops_enabled = true
approve_commands = ["approve"]
unapprove_commands = ["unapprove"]
"""

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_repo_config_uses_default_branch(webhook_handler, mock_github_client):
    """Test repo config is loaded from the default branch."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["repository"]["default_branch"] = "develop"
    payload["pull_request"]["base"]["ref"] = "release"

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    args = mock_github_client.get_repo_file.call_args[0]
    assert args[3] == "develop"


@pytest.mark.asyncio
async def test_org_github_repo_config_fallback(webhook_handler, mock_github_client):
    """Test org .github stampbot.toml is used when repo config is missing."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["repository"]["full_name"] = "acme/widgets"
    payload["repository"]["default_branch"] = "main"
    payload["repository"]["owner"] = {"login": "acme", "type": "Organization"}
    payload["pull_request"]["labels"] = [{"name": "org-approve"}]

    def get_repo_file_side_effect(_installation_id, repo_full_name, _file_path, _ref):
        if repo_full_name == "acme/widgets":
            return None
        if repo_full_name == "acme/.github":
            return 'approval_labels = ["org-approve"]'
        return None

    mock_github_client.get_repo_file.side_effect = get_repo_file_side_effect

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()
    assert any(
        call_args[0][1] == "acme/.github" and call_args[0][3] is None
        for call_args in mock_github_client.get_repo_file.call_args_list
    )


@pytest.mark.asyncio
async def test_org_github_repo_config_missing_uses_defaults(webhook_handler, mock_github_client):
    """Test org .github missing falls back to default config."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["repository"]["full_name"] = "acme/widgets"
    payload["repository"]["default_branch"] = "main"
    payload["repository"]["owner"] = {"login": "acme", "type": "Organization"}

    def get_repo_file_side_effect(_installation_id, repo_full_name, _file_path, _ref):
        if repo_full_name in ("acme/widgets", "acme/.github"):
            return None
        return None

    mock_github_client.get_repo_file.side_effect = get_repo_file_side_effect

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    assert any(
        call_args[0][1] == "acme/.github" and call_args[0][3] is None
        for call_args in mock_github_client.get_repo_file.call_args_list
    )


@pytest.mark.asyncio
async def test_chatops_disabled_in_config(webhook_handler, mock_github_client):
    """Test chatops commands ignored when disabled in config."""
    payload = load_fixture("issue_comment_approve")

    # Mock config with chatops disabled
    mock_github_client.get_repo_file.return_value = """
approval_labels = ["autoapprove"]
chatops_enabled = false
"""

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "not enabled" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()
    mock_github_client.user_has_permission.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_repo_config_posts_review(webhook_handler, mock_github_client):
    """Test invalid repo config logs and posts a review comment."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.return_value = 'chatops_required_permission = "invalid"'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"
    assert "invalid" in result["message"].lower()
    mock_github_client.create_pr_review_comment.assert_called_once()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_repo_config_no_review_on_non_opened(webhook_handler, mock_github_client):
    """Test invalid config skips review comment for non-opened events."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["action"] = "labeled"
    mock_github_client.get_repo_file.return_value = 'chatops_required_permission = "invalid"'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"
    mock_github_client.create_pr_review_comment.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_repo_config_blocks_chatops(webhook_handler, mock_github_client):
    """Test invalid repo config blocks chatops actions."""
    payload = load_fixture("issue_comment_approve")
    mock_github_client.get_repo_file.return_value = 'chatops_required_permission = "invalid"'

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "error"
    mock_github_client.user_has_permission.assert_not_called()


# =============================================================================
# Signature Verification Tests
# =============================================================================


def test_verify_signature_valid(webhook_handler):
    """Test valid signature verification."""
    import hashlib
    import hmac

    payload = b'{"test": "data"}'
    signature = (
        "sha256="
        + hmac.new(
            webhook_handler.webhook_secret,
            payload,
            hashlib.sha256,
        ).hexdigest()
    )

    assert webhook_handler.verify_signature(payload, signature) is True


def test_verify_signature_invalid(webhook_handler):
    """Test invalid signature is rejected."""
    payload = b'{"test": "data"}'
    signature = "sha256=invalidsignature"

    assert webhook_handler.verify_signature(payload, signature) is False


def test_verify_signature_non_ascii(webhook_handler):
    """Test non-ASCII signature content is rejected."""
    payload = b'{"test": "data"}'
    signature = "sha256=\u2603"

    assert webhook_handler.verify_signature(payload, signature) is False


def test_verify_signature_missing(webhook_handler):
    """Test missing signature is rejected."""
    payload = b'{"test": "data"}'

    assert webhook_handler.verify_signature(payload, None) is False
    assert webhook_handler.verify_signature(payload, "") is False


# =============================================================================
# Additional Coverage Tests
# =============================================================================


def test_webhook_secret_not_configured():
    """Test RuntimeError when webhook secret is not configured."""
    from stampbot.webhook_handler import WebhookHandler

    handler = WebhookHandler()
    handler._webhook_secret = None  # Reset

    with patch("stampbot.webhook_handler.settings") as mock_settings:
        mock_settings.webhook_secret = None

        with pytest.raises(RuntimeError, match="Webhook secret not configured"):
            _ = handler.webhook_secret


@pytest.mark.asyncio
async def test_pull_request_review_comment_event(webhook_handler, mock_github_client):
    """Test handling pull_request_review_comment event type."""
    payload = {
        "action": "created",
        "pull_request": {
            "number": 42,
        },
        "comment": {
            "body": "@stampbot approve",
            "user": {"login": "testuser"},
        },
        "repository": {
            "full_name": "owner/repo",
        },
        "installation": {"id": 12345},
    }

    result = await webhook_handler.handle_event("pull_request_review_comment", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_pr_comment_from_pull_request_payload(webhook_handler, mock_github_client):
    """Test chatops from pull_request in payload (not issue)."""
    # This tests lines 271-274 - the elif branch for pull_request in payload
    payload = {
        "action": "created",
        "pull_request": {
            "number": 99,
        },
        "comment": {
            "body": "@stampbot approve",
            "user": {"login": "testuser"},
        },
        "repository": {
            "full_name": "owner/repo",
        },
        "installation": {"id": 12345},
    }

    result = await webhook_handler.handle_event("pull_request_review_comment", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_pr_comment_missing_fields(webhook_handler, mock_github_client):
    """Test chatops with missing required fields."""
    payload = {
        "action": "created",
        "issue": {
            "number": 1,
            "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/1"},
        },
        "comment": {
            "body": "@stampbot approve",
            "user": {"login": "testuser"},
        },
        "repository": {
            "full_name": "owner/repo",
        },
        # Missing installation
    }

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "error"
    assert "missing" in result["message"].lower()


@pytest.mark.asyncio
async def test_pr_comment_no_command_after_mention(webhook_handler, mock_github_client):
    """Test @stampbot mention without a command word."""
    payload = {
        "action": "created",
        "issue": {
            "number": 1,
            "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/1"},
        },
        "comment": {
            "body": "@stampbot",  # No command after mention
            "user": {"login": "testuser"},
        },
        "repository": {
            "full_name": "owner/repo",
        },
        "installation": {"id": 12345},
    }

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "ignored"
    assert "no command" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_repo_config_exception(webhook_handler, mock_github_client):
    """Test _get_repo_config handles exceptions gracefully."""
    payload = load_fixture("pr_opened_with_autoapprove_label")

    # Make get_repo_file raise an exception
    mock_github_client.get_repo_file.side_effect = Exception("Network error")

    # Should still work, using defaults
    result = await webhook_handler.handle_event("pull_request", payload)

    # With defaults, autoapprove label should trigger approval
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_dismiss_approvals_exception(webhook_handler, mock_github_client):
    """Test _dismiss_approvals handles exceptions gracefully."""
    payload = load_fixture("pr_unlabeled_autoapprove")

    # Make find_bot_reviews raise an exception
    mock_github_client.find_bot_reviews.side_effect = Exception("API error")

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_pr_comment_not_pr_error(webhook_handler, mock_github_client):
    """Test chatops returns error when neither issue.pull_request nor pull_request exists."""
    payload = {
        "action": "created",
        "comment": {
            "body": "@stampbot approve",
            "user": {"login": "testuser"},
        },
        "repository": {
            "full_name": "owner/repo",
        },
        "installation": {"id": 12345},
    }

    result = await webhook_handler.handle_event("pull_request_review_comment", payload)

    assert result["status"] == "error"
    assert "not a pr" in result["message"].lower()


@pytest.mark.asyncio
async def test_pr_with_non_approval_labels(webhook_handler, mock_github_client):
    """Test PR with labels that don't match any approval labels.

    This tests the branch where the for loop completes without finding
    a matching approval label (line 167->166).
    """
    payload = load_fixture("pr_opened_with_autoapprove_label")
    # Change labels to ones that don't trigger approval
    payload["pull_request"]["labels"] = [
        {"name": "bug"},
        {"name": "documentation"},
        {"name": "help-wanted"},
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "no action" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_unlabeled_non_approval_label(webhook_handler, mock_github_client):
    """Test unlabeled event where removed label is NOT an approval label.

    This tests the branch where removed_label is not in approval_labels (line 209->246).
    """
    payload = load_fixture("pr_unlabeled_autoapprove")
    # Change the removed label to a non-approval label
    payload["label"] = {"name": "bug"}

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "no action" in result["message"].lower()
    mock_github_client.find_bot_reviews.assert_not_called()
    mock_github_client.dismiss_approval.assert_not_called()


# =============================================================================
# Team Membership Filter Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pr_with_allowed_teams_triggers_team_check(webhook_handler, mock_github_client):
    """Test PR with allowed_teams configured triggers team membership check."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    # Config with allowed_teams - user not in allowed_users so needs team check
    mock_github_client.get_repo_file.return_value = """
allowed_teams = ["acme/release-team"]
"""
    # User is in the allowed team
    mock_github_client.get_user_team_slugs.return_value = ["release-team"]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.get_user_team_slugs.assert_called_once()
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_pr_with_allowed_teams_user_not_member(webhook_handler, mock_github_client):
    """Test PR rejected when user is not in any allowed team."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.return_value = """
allowed_teams = ["acme/release-team"]
"""
    # User is NOT in the allowed team
    mock_github_client.get_user_team_slugs.return_value = []

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "not eligible" in result["message"].lower()
    mock_github_client.get_user_team_slugs.assert_called_once()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_with_allowed_users_skips_team_check(webhook_handler, mock_github_client):
    """Test PR with user in allowed_users skips team membership check."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    # The fixture has user "contributor"
    mock_github_client.get_repo_file.return_value = """
allowed_users = ["contributor"]
allowed_teams = ["acme/release-team"]
"""

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    # Should NOT call get_user_team_slugs since user is in allowed_users
    mock_github_client.get_user_team_slugs.assert_not_called()
    mock_github_client.approve_pr.assert_called_once()
