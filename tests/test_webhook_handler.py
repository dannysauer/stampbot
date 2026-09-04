"""Tests for webhook handler."""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from github.GithubException import GithubException

from tests.conftest import load_fixture


@pytest.fixture
def mock_github_client():
    """Create a mock GitHub client, visible to the handler and the policy resolver."""
    with (
        patch("stampbot.webhook_handler.github_client") as mock,
        patch("stampbot.repo_policy.github_client", mock),
    ):
        # Default successful responses
        mock.get_repo_file.return_value = None  # No config file, use defaults
        mock.approve_pr.return_value = True
        mock.create_pr_review_comment.return_value = True
        mock.create_issue_comment.return_value = True
        mock.dismiss_approval.return_value = True
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


def _remember_approval(mock_github_client, review_id=777, commit_id="abc123def456"):
    """Make later review scans see an approval, as GitHub would after a create_review."""

    def approve_and_remember(*_args, **_kwargs):
        mock_github_client.find_bot_approval_reviews.return_value = [
            {"id": review_id, "state": "APPROVED", "commit_id": commit_id}
        ]
        return True

    mock_github_client.approve_pr.side_effect = approve_and_remember


@pytest.mark.asyncio
@pytest.mark.parametrize("labeled_first", [False, True])
async def test_pr_labeled_at_creation_approves_once(
    webhook_handler, mock_github_client, labeled_first
):
    """Test the opened and labeled events for one new pull request yield one approval.

    GitHub delivers the two events in either order. When the second event can
    see the first approval, the existing-approval check stops it.
    """
    opened = load_fixture("pr_opened_with_autoapprove_label")
    labeled = load_fixture("pr_labeled_autoapprove")
    events = [labeled, opened] if labeled_first else [opened, labeled]
    _remember_approval(mock_github_client)

    results = [await webhook_handler.handle_event("pull_request", e) for e in events]

    assert [r["status"] for r in results] == ["success", "success"]
    mock_github_client.approve_pr.assert_called_once()
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.asyncio
async def test_pr_opened_and_labeled_race_leaves_one_approval(webhook_handler, mock_github_client):
    """Test two replicas that both approved converge on one active approval.

    Neither replica saw the other's review before approving. After approving,
    each re-reads the reviews and dismisses every approval of the head except
    the oldest, so both reach the same answer.
    """
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.find_bot_approval_reviews.side_effect = [
        [],  # pre-check: nothing yet
        [  # post-approval scan: both replicas have posted
            {"id": 200, "state": "APPROVED", "commit_id": "abc123def456"},
            {"id": 100, "state": "APPROVED", "commit_id": "abc123def456"},
            {"id": 50, "state": "APPROVED", "commit_id": "oldsha"},  # stale, not a duplicate
            {"id": 60, "state": "DISMISSED", "commit_id": "abc123def456"},
        ],
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()
    mock_github_client.dismiss_approval.assert_called_once_with(
        12345, "octocat/hello-world", 42, 200, "Duplicate Stampbot approval"
    )


@pytest.mark.asyncio
async def test_pr_duplicate_check_failure_keeps_approval(webhook_handler, mock_github_client):
    """Test a failed duplicate scan never turns a posted approval into an error."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.find_bot_approval_reviews.side_effect = [
        [],
        RuntimeError("GitHub unavailable"),
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.parametrize(
    ("reviews", "head_sha", "expected"),
    [
        ([], "head", []),
        ([{"id": 1, "state": "APPROVED", "commit_id": "head"}], "head", [1]),
        (
            [
                {"id": 3, "state": "APPROVED", "commit_id": "head"},
                {"id": 1, "state": "APPROVED", "commit_id": "head"},
                {"id": 2, "state": "APPROVED", "commit_id": "head"},
            ],
            "head",
            [1, 2, 3],
        ),
        (
            [
                {"id": 1, "state": "APPROVED", "commit_id": "old"},
                {"id": 2, "state": "APPROVED", "commit_id": "head"},
            ],
            "head",
            [2],
        ),
        (
            [
                {"id": 1, "state": "APPROVED", "commit_id": None},
                {"id": 2, "state": "APPROVED", "commit_id": "head"},
            ],
            None,
            [1, 2],
        ),
        (
            [
                {"id": 1, "state": "DISMISSED", "commit_id": "head"},
                {"id": 2, "state": "APPROVED", "commit_id": "head"},
            ],
            "head",
            [2],
        ),
    ],
)
def test_active_approvals_for_head(webhook_handler, reviews, head_sha, expected):
    """Test which approvals count as covering the head, oldest first."""
    assert webhook_handler._active_approvals_for_head(reviews, head_sha) == expected


@pytest.mark.parametrize(
    ("reviews", "head_sha", "expected"),
    [
        ([{"id": 1, "state": "APPROVED", "commit_id": "head"}], None, []),
        ([{"id": 1, "state": "APPROVED", "commit_id": None}], "head", []),
        (
            [
                {"id": 2, "state": "APPROVED", "commit_id": "head"},
                {"id": 1, "state": "APPROVED", "commit_id": None},
                {"id": 3, "state": "APPROVED", "commit_id": "head"},
            ],
            "head",
            [2, 3],
        ),
    ],
)
def test_active_approvals_for_head_proven_only(webhook_handler, reviews, head_sha, expected):
    """Test duplicate cleanup never selects an approval on a guess."""
    assert (
        webhook_handler._active_approvals_for_head(reviews, head_sha, proven_only=True) == expected
    )


@pytest.mark.asyncio
async def test_chatops_approve_without_head_skips_duplicate_cleanup(
    webhook_handler, mock_github_client
):
    """Test no dismissal happens when the head is unknown, even with several approvals."""
    payload = load_fixture("issue_comment_approve")
    mock_github_client.get_pr_head_sha.return_value = None
    mock_github_client.find_bot_approval_reviews.side_effect = [
        [],
        [
            {"id": 1, "state": "APPROVED", "commit_id": "a"},
            {"id": 2, "state": "APPROVED", "commit_id": "b"},
        ],
    ]

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.asyncio
async def test_pr_labeled_second_approval_label_also_approves(webhook_handler, mock_github_client):
    """Test a second configured label still approves when no approval exists yet."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["pull_request"]["labels"] = [{"name": "autoapprove"}, {"name": "stamp"}]
    payload["label"] = {"name": "stamp"}

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_pr_reopened_with_autoapprove_label(webhook_handler, mock_github_client):
    """Test reopening a labeled pull request approves it; no labeled event accompanies reopen."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["action"] = "reopened"

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    # Once before approving, once afterwards to look for duplicates.
    assert mock_github_client.find_bot_approval_reviews.call_count == 2
    mock_github_client.approve_pr.assert_called_once()


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
    """Test missing approval label triggers label check after the review decision."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.repo_has_label.return_value = False

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    # The label on the pull request exists by definition; only "stamp" is checked.
    mock_github_client.repo_has_label.assert_called_once_with(12345, "octocat/hello-world", "stamp")
    # The lookup runs after the approval so it never delays the review.
    call_names = [name for name, _args, _kwargs in mock_github_client.mock_calls]
    assert call_names.index("approve_pr") < call_names.index("repo_has_label")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edited", "closed", "review_requested", "ready_for_review"])
async def test_pr_action_without_review_effect_skips_policy_read(
    webhook_handler, mock_github_client, action
):
    """Test actions that cannot change review state make no GitHub requests."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    payload["action"] = action

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert action in result["message"]
    mock_github_client.get_repo_file.assert_not_called()
    mock_github_client.approve_pr.assert_not_called()
    mock_github_client.repo_has_label.assert_not_called()


@pytest.mark.asyncio
async def test_repo_config_is_cached_between_events(webhook_handler, mock_github_client):
    """Test a second event for the same repository reuses the parsed policy."""
    payload = load_fixture("pr_labeled_autoapprove")

    first = await webhook_handler.handle_event("pull_request", payload)
    second = await webhook_handler.handle_event("pull_request", payload)

    assert first["status"] == "success"
    assert second["status"] == "success"
    mock_github_client.get_repo_file.assert_called_once()
    assert mock_github_client.approve_pr.call_count == 2


@pytest.mark.asyncio
async def test_repo_config_cache_disabled(mock_github_client, monkeypatch):
    """Test a zero cache lifetime reads policy on every event."""
    monkeypatch.setattr("stampbot.webhook_handler.repo_config_cache_seconds", lambda: 0)
    from stampbot.webhook_handler import WebhookHandler

    handler = WebhookHandler()
    payload = load_fixture("pr_labeled_autoapprove")

    await handler.handle_event("pull_request", payload)
    await handler.handle_event("pull_request", payload)

    assert mock_github_client.get_repo_file.call_count == 2


@pytest.mark.asyncio
async def test_repo_config_cache_key_includes_default_branch(webhook_handler, mock_github_client):
    """Test policy read from another default branch is not served from the cache."""
    payload = load_fixture("pr_labeled_autoapprove")

    await webhook_handler.handle_event("pull_request", payload)
    payload["repository"]["default_branch"] = "develop"
    await webhook_handler.handle_event("pull_request", payload)

    assert mock_github_client.get_repo_file.call_count == 2


@pytest.mark.asyncio
async def test_invalid_repo_config_is_not_cached(webhook_handler, mock_github_client):
    """Test a policy error is re-read on the next event so fixes apply at once."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.get_repo_file.side_effect = [
        'chatops_required_permission = "owner"',
        None,
    ]

    first = await webhook_handler.handle_event("pull_request", payload)
    second = await webhook_handler.handle_event("pull_request", payload)

    assert first["status"] == "error"
    assert second["status"] == "success"
    assert mock_github_client.get_repo_file.call_count == 2


@pytest.mark.asyncio
async def test_handle_event_records_delivery_id(webhook_handler):
    """Test the GitHub delivery GUID is attached to the event span."""
    with patch("stampbot.webhook_handler.create_span") as mock_span:
        mock_span.return_value.__enter__ = MagicMock(return_value=None)
        mock_span.return_value.__exit__ = MagicMock(return_value=False)

        await webhook_handler.handle_event("ping", {"zen": "x"}, delivery_id="abc-123")

    attributes = mock_span.call_args_list[0].args[1]
    assert attributes["github.delivery_id"] == "abc-123"
    assert attributes["webhook.event_type"] == "ping"


@pytest.mark.asyncio
async def test_handle_event_omits_missing_delivery_id(webhook_handler):
    """Test no delivery attribute is recorded when GitHub sent none."""
    with patch("stampbot.webhook_handler.create_span") as mock_span:
        mock_span.return_value.__enter__ = MagicMock(return_value=None)
        mock_span.return_value.__exit__ = MagicMock(return_value=False)

        await webhook_handler.handle_event("ping", {"zen": "x"})

    attributes = mock_span.call_args_list[0].args[1]
    assert "github.delivery_id" not in attributes


@pytest.mark.asyncio
async def test_pr_label_ignored_when_auto_approve_disabled(webhook_handler, mock_github_client):
    """Test label approvals are ignored when auto_approve_on_label is disabled."""
    payload = load_fixture("pr_labeled_autoapprove")
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


@pytest.mark.parametrize(
    ("action", "event_label", "labels", "reapprove", "expected"),
    [
        ("opened", None, ["autoapprove"], False, ("approve", "autoapprove")),
        ("opened", None, [], False, ("none", None)),
        ("reopened", None, ["stamp"], False, ("approve", "stamp")),
        ("labeled", "autoapprove", ["autoapprove"], False, ("approve", "autoapprove")),
        ("labeled", "stamp", ["autoapprove", "stamp"], False, ("approve", "autoapprove")),
        ("labeled", "autoapprove", ["stamp", "autoapprove"], False, ("approve", "autoapprove")),
        ("labeled", "bug", ["autoapprove"], False, ("refresh", "autoapprove")),
        ("labeled", "bug", [], False, ("none", None)),
        ("synchronize", None, ["autoapprove"], True, ("refresh", "autoapprove")),
        ("synchronize", None, ["autoapprove"], False, ("none", None)),
        ("unlabeled", "autoapprove", [], False, ("dismiss", "autoapprove")),
        ("unlabeled", "bug", ["autoapprove"], False, ("none", None)),
    ],
)
def test_decide(webhook_handler, action, event_label, labels, reapprove, expected):
    """Test the pure decision for every pull request action Stampbot handles."""
    from stampbot.config import RepoConfig
    from stampbot.webhook_handler import PullRequestEvent

    config = RepoConfig.from_toml(f"reapprove = {str(reapprove).lower()}")
    event = PullRequestEvent(
        action=action,
        installation_id=12345,
        repo_full_name="octocat/hello-world",
        pr_number=42,
        owner_login="octocat",
        pr={"labels": [{"name": name} for name in labels]},
        event_label=event_label,
        repo_config=config,
    )

    decision, label = webhook_handler._decide(event)

    assert (decision.value, label) == expected


def test_decide_respects_auto_approve_disabled(webhook_handler):
    """Test label events do nothing when label-driven approval is off."""
    from stampbot.config import RepoConfig
    from stampbot.webhook_handler import Decision, PullRequestEvent

    config = RepoConfig.from_toml("auto_approve_on_label = false")
    event = PullRequestEvent(
        action="unlabeled",
        installation_id=12345,
        repo_full_name="octocat/hello-world",
        pr_number=42,
        owner_login="octocat",
        pr={"labels": []},
        event_label="autoapprove",
        repo_config=config,
    )

    assert webhook_handler._decide(event) == (Decision.NONE, None)


@pytest.mark.asyncio
async def test_pr_labeled_skips_duplicate_approval(webhook_handler, mock_github_client):
    """Test that existing approval prevents duplicate approval comment."""
    payload = load_fixture("pr_labeled_autoapprove")
    # Simulate an existing active approval of the current head
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 12345, "state": "APPROVED", "commit_id": "abc123def456"}
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.find_bot_approval_reviews.assert_called_once()
    # approve_pr should NOT be called since there's already an approval
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_labeled_stale_approval_does_not_block(webhook_handler, mock_github_client):
    """Test re-adding a label approves a new head that only a stale approval covers."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 12345, "state": "APPROVED", "commit_id": "oldsha"}
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_pr_unlabeled_autoapprove(webhook_handler, mock_github_client):
    """Test removing autoapprove label dismisses approvals."""
    payload = load_fixture("pr_unlabeled_autoapprove")
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 111, "state": "APPROVED", "commit_id": "abc123def456"},
        {"id": 222, "state": "APPROVED", "commit_id": "abc123def456"},
    ]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    assert "dismissed" in result["message"].lower()
    mock_github_client.find_bot_approval_reviews.assert_called_once()
    assert mock_github_client.dismiss_approval.call_count == 2


@pytest.mark.asyncio
async def test_pr_unlabeled_ignored_when_auto_approve_disabled(webhook_handler, mock_github_client):
    """Test label removal is ignored when auto_approve_on_label is disabled."""
    payload = load_fixture("pr_unlabeled_autoapprove")
    mock_github_client.get_repo_file.return_value = "auto_approve_on_label = false"

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.asyncio
async def test_pr_unlabeled_no_bot_reviews(webhook_handler, mock_github_client):
    """Test removing label when no bot reviews exist."""
    payload = load_fixture("pr_unlabeled_autoapprove")
    mock_github_client.find_bot_approval_reviews.return_value = []  # No reviews to dismiss

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.dismiss_approval.assert_not_called()


@pytest.mark.asyncio
async def test_pr_approval_failure(webhook_handler, mock_github_client):
    """Test handling approval failure."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.approve_pr.return_value = False  # Simulate failure

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"
    # A failed approval leaves nothing to deduplicate.
    assert mock_github_client.find_bot_approval_reviews.call_count == 1
    assert "failed" in result["message"].lower()


@pytest.mark.asyncio
async def test_pr_missing_installation_id(webhook_handler, mock_github_client):
    """Test PR event with missing installation ID."""
    payload = load_fixture("pr_labeled_autoapprove")
    del payload["installation"]

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "error"
    assert "missing" in result["message"].lower()


@pytest.mark.asyncio
async def test_pr_not_eligible_missing_required_label(webhook_handler, mock_github_client):
    """Test PR with approval label but missing required label is not approved."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.get_repo_file.return_value = 'required_labels = ["dependencies"]'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "not eligible" in result["message"].lower()
    assert "missing required label" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_not_eligible_title_pattern_no_match(webhook_handler, mock_github_client):
    """Test PR with approval label but title doesn't match pattern is not approved."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.get_repo_file.return_value = 'required_title_patterns = ["^chore:"]'

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "ignored"
    assert "not eligible" in result["message"].lower()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pathological_pr_title_pattern_is_bounded(webhook_handler, mock_github_client):
    """Test a pathological title pattern fails closed within a bounded time."""
    from stampbot.config import MAX_PR_TITLE_LENGTH

    payload = load_fixture("pr_labeled_autoapprove")
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

    payload = load_fixture("pr_labeled_autoapprove")
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
    payload = load_fixture("pr_labeled_autoapprove")
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
    # Once before approving, once afterwards to look for duplicates.
    assert mock_github_client.find_bot_approval_reviews.call_count == 2
    mock_github_client.approve_pr.assert_called_once()


@pytest.mark.asyncio
async def test_issue_comment_approve_without_head_uses_any_active_approval(
    webhook_handler, mock_github_client
):
    """Test the existing-approval check falls back to any active review when the head is unknown."""
    payload = load_fixture("issue_comment_approve")
    mock_github_client.get_pr_head_sha.return_value = None
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 4242, "state": "APPROVED", "commit_id": "whatever"}
    ]

    result = await webhook_handler.handle_event("issue_comment", payload)

    assert result["status"] == "success"
    mock_github_client.find_bot_approval_reviews.assert_called_once()
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_pr_duplicate_dismissal_failure_is_counted(webhook_handler, mock_github_client):
    """Test a dismissal that GitHub rejects is recorded without failing the approval."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.find_bot_approval_reviews.side_effect = [
        [],
        [
            {"id": 100, "state": "APPROVED", "commit_id": "abc123def456"},
            {"id": 200, "state": "APPROVED", "commit_id": "abc123def456"},
        ],
    ]
    mock_github_client.dismiss_approval.return_value = False
    from stampbot.metrics import pr_dismissals_total

    failures = pr_dismissals_total.labels(trigger_type="duplicate", status="failure")
    before = failures._value.get()

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    mock_github_client.dismiss_approval.assert_called_once()
    assert failures._value.get() == before + 1


@pytest.mark.asyncio
async def test_issue_comment_unapprove(webhook_handler, mock_github_client):
    """Test @stampbot unapprove command."""
    payload = load_fixture("issue_comment_unapprove")
    mock_github_client.find_bot_approval_reviews.return_value = [
        {"id": 111, "state": "APPROVED", "commit_id": "abc123def456"}
    ]

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
    payload = load_fixture("pr_labeled_autoapprove")
    # Change label to custom one
    payload["pull_request"]["labels"] = [{"name": "custom-approve"}]
    payload["label"] = {"name": "custom-approve"}

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
    payload = load_fixture("pr_labeled_autoapprove")
    payload["repository"]["default_branch"] = "develop"
    payload["pull_request"]["base"]["ref"] = "release"

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result["status"] == "success"
    args = mock_github_client.get_repo_file.call_args[0]
    assert args[3] == "develop"


@pytest.mark.asyncio
async def test_org_github_repo_config_fallback(webhook_handler, mock_github_client):
    """Test org .github stampbot.toml is used when repo config is missing."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["repository"]["full_name"] = "acme/widgets"
    payload["repository"]["default_branch"] = "main"
    payload["repository"]["owner"] = {"login": "acme", "type": "Organization"}
    payload["pull_request"]["labels"] = [{"name": "org-approve"}]
    payload["label"] = {"name": "org-approve"}

    def get_repo_file_side_effect(
        _installation_id,
        repo_full_name,
        _file_path,
        _ref,
        *,
        missing_repository_is_optional=False,
    ):
        if repo_full_name == "acme/widgets":
            assert missing_repository_is_optional is False
            return None
        if repo_full_name == "acme/.github":
            assert missing_repository_is_optional is True
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
    payload = load_fixture("pr_labeled_autoapprove")
    payload["repository"]["full_name"] = "acme/widgets"
    payload["repository"]["default_branch"] = "main"
    payload["repository"]["owner"] = {"login": "acme", "type": "Organization"}

    def get_repo_file_side_effect(
        _installation_id,
        repo_full_name,
        _file_path,
        _ref,
        *,
        missing_repository_is_optional=False,
    ):
        if repo_full_name in ("acme/widgets", "acme/.github"):
            assert missing_repository_is_optional is (repo_full_name == "acme/.github")
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
async def test_target_repo_not_found_fails_closed(webhook_handler, mock_github_client):
    """Test a target-repository 404 never advances to service defaults."""
    payload = load_fixture("pr_labeled_autoapprove")
    mock_github_client.get_repo_file.side_effect = GithubException(
        404,
        {"message": "Not Found"},
        None,
    )

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        GithubException(403, {"message": "Forbidden"}, None),
        GithubException(500, {"message": "Server Error"}, None),
        TimeoutError("GitHub read timed out"),
    ],
)
async def test_org_policy_read_failure_fails_closed(
    webhook_handler,
    mock_github_client,
    failure,
):
    """Test only a missing optional organization repository permits defaults."""
    payload = load_fixture("pr_labeled_autoapprove")
    payload["repository"]["full_name"] = "acme/widgets"
    payload["repository"]["owner"] = {"login": "acme", "type": "Organization"}

    def get_repo_file_side_effect(
        _installation_id,
        repo_full_name,
        _file_path,
        _ref,
        *,
        missing_repository_is_optional=False,
    ):
        if repo_full_name == "acme/widgets":
            assert missing_repository_is_optional is False
            return None
        assert repo_full_name == "acme/.github"
        assert missing_repository_is_optional is True
        raise failure

    mock_github_client.get_repo_file.side_effect = get_repo_file_side_effect

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_missing_repo_config_with_invalid_service_defaults_fails_closed(
    webhook_handler, mock_github_client
):
    """Test invalid service defaults block a PR when no repo config exists."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    service_defaults = MagicMock(spec=["approve_commands"])
    service_defaults.approve_commands = "approve"

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = service_defaults
        mock_settings.defaults = service_defaults

        result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    review_body = mock_github_client.create_pr_review_comment.call_args.args[3]
    assert "Invalid service default configuration" in review_body
    assert "approve_commands must be a list of strings" in review_body
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_service_default_permission_fails_closed(
    webhook_handler,
    mock_github_client,
):
    """Test an unknown service-wide ChatOps permission blocks automation."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    service_defaults = MagicMock(spec=["chatops_required_permission"])
    service_defaults.chatops_required_permission = "owner"

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = service_defaults
        mock_settings.defaults = service_defaults

        result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    review_body = mock_github_client.create_pr_review_comment.call_args.args[3]
    assert "Invalid service default configuration" in review_body
    assert "Invalid chatops_required_permission" in review_body
    mock_github_client.approve_pr.assert_not_called()


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


@pytest.mark.asyncio
async def test_invalid_repo_and_service_configs_use_builtin_fail_closed_config(
    webhook_handler, mock_github_client
):
    """Test repo errors do not re-read invalid service defaults for fallback."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.return_value = 'chatops_required_permission = "invalid"'
    service_defaults = MagicMock(spec=["approve_commands"])
    service_defaults.approve_commands = "approve"

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = service_defaults
        mock_settings.defaults = service_defaults

        result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    review_body = mock_github_client.create_pr_review_comment.call_args.args[3]
    assert "Invalid chatops_required_permission" in review_body
    mock_github_client.approve_pr.assert_not_called()


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
async def test_get_repo_config_exception_fails_closed(webhook_handler, mock_github_client):
    """Test config fetch failures disable automation with valid defaults."""
    payload = load_fixture("pr_opened_with_autoapprove_label")

    mock_github_client.get_repo_file.side_effect = Exception("Network error")

    result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    review_body = mock_github_client.create_pr_review_comment.call_args.args[3]
    assert "Unable to load Stampbot configuration" in review_body
    assert "Network error" not in review_body
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_get_repo_config_exception_with_invalid_service_defaults_fails_closed(
    webhook_handler, mock_github_client
):
    """Test fetch failures cannot escape invalid service defaults as a 500."""
    payload = load_fixture("pr_opened_with_autoapprove_label")
    mock_github_client.get_repo_file.side_effect = Exception("Network error")
    service_defaults = MagicMock(spec=["unapprove_commands"])
    service_defaults.unapprove_commands = ["unapprove", 1]

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.return_value = service_defaults
        mock_settings.defaults = service_defaults

        result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    review_body = mock_github_client.create_pr_review_comment.call_args.args[3]
    assert "Unable to load Stampbot configuration" in review_body
    assert "Network error" not in review_body
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_non_validation_error_reading_service_defaults_fails_closed(
    webhook_handler, mock_github_client
):
    """Test unexpected settings failures cannot escape the safe fallback."""
    payload = load_fixture("pr_opened_with_autoapprove_label")

    with patch("stampbot.config.settings") as mock_settings:
        mock_settings.get.side_effect = RuntimeError("settings backend failed")

        result = await webhook_handler.handle_event("pull_request", payload)

    assert result == {"status": "error", "message": "Invalid repository configuration"}
    review_body = mock_github_client.create_pr_review_comment.call_args.args[3]
    assert "Unable to load Stampbot configuration" in review_body
    assert "settings backend failed" not in review_body
    mock_github_client.approve_pr.assert_not_called()


@pytest.mark.asyncio
async def test_dismiss_approvals_exception(webhook_handler, mock_github_client):
    """Test _dismiss_approvals handles exceptions gracefully."""
    payload = load_fixture("pr_unlabeled_autoapprove")

    mock_github_client.find_bot_approval_reviews.side_effect = Exception("API error")

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
    payload = load_fixture("pr_labeled_autoapprove")
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
    mock_github_client.dismiss_approval.assert_not_called()


# =============================================================================
# Team Membership Filter Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pr_with_allowed_teams_triggers_team_check(webhook_handler, mock_github_client):
    """Test PR with allowed_teams configured triggers team membership check."""
    payload = load_fixture("pr_labeled_autoapprove")
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
    payload = load_fixture("pr_labeled_autoapprove")
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
    payload = load_fixture("pr_labeled_autoapprove")
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
