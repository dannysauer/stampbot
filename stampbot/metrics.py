# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Prometheus metrics for stampbot."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, Info, generate_latest

# Use the default registry for compatibility with multiprocess mode
registry = REGISTRY

# =============================================================================
# Application Info
# =============================================================================

app_info = Info(
    "stampbot",
    "Stampbot application information",
)

# =============================================================================
# HTTP Metrics (Standard Web Service)
# =============================================================================

http_requests_total = Counter(
    "stampbot_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "stampbot_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

http_request_size_bytes = Histogram(
    "stampbot_http_request_size_bytes",
    "HTTP request body size in bytes",
    ["method", "endpoint"],
    buckets=(100, 500, 1000, 5000, 10000, 50000, 100000, 500000),
)

http_response_size_bytes = Histogram(
    "stampbot_http_response_size_bytes",
    "HTTP response body size in bytes",
    ["method", "endpoint"],
    buckets=(100, 500, 1000, 5000, 10000, 50000, 100000, 500000),
)

http_requests_in_progress = Gauge(
    "stampbot_http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "endpoint"],
)

# =============================================================================
# Webhook Metrics
# =============================================================================

webhook_events_total = Counter(
    "stampbot_webhook_events_total",
    "Total webhook events received",
    ["event_type", "action"],
)

webhook_signature_validations_total = Counter(
    "stampbot_webhook_signature_validations_total",
    "Total webhook signature validations",
    ["result"],  # valid, invalid
)

webhook_processing_duration_seconds = Histogram(
    "stampbot_webhook_processing_duration_seconds",
    "Webhook event processing duration in seconds",
    ["event_type"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# =============================================================================
# PR Approval Metrics
# =============================================================================

pr_approvals_total = Counter(
    "stampbot_pr_approvals_total",
    "Total PR approvals attempted",
    ["trigger_type", "status"],  # trigger: label, chatops; status: success, failure
)

pr_approval_duration_seconds = Histogram(
    "stampbot_pr_approval_duration_seconds",
    "PR approval operation duration in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

pr_dismissals_total = Counter(
    "stampbot_pr_dismissals_total",
    "Total PR approval dismissals attempted",
    ["trigger_type", "status"],  # trigger: label_removed, chatops; status: success, failure
)

pr_dismissal_duration_seconds = Histogram(
    "stampbot_pr_dismissal_duration_seconds",
    "PR dismissal operation duration in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# =============================================================================
# ChatOps Metrics
# =============================================================================

chatops_commands_total = Counter(
    "stampbot_chatops_commands_total",
    "Total ChatOps commands received",
    [
        "command",
        "status",
    ],  # command: approve, unapprove, unknown; status: success, failure, ignored
)

# =============================================================================
# GitHub API Metrics
# =============================================================================

github_api_requests_total = Counter(
    "stampbot_github_api_requests_total",
    "Total GitHub API requests",
    ["operation", "status"],  # operation: approve, dismiss, get_file, find_reviews, get_token
)

github_api_request_duration_seconds = Histogram(
    "stampbot_github_api_request_duration_seconds",
    "GitHub API request duration in seconds",
    ["operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

github_api_rate_limit_remaining = Gauge(
    "stampbot_github_api_rate_limit_remaining",
    "Remaining GitHub API rate limit",
    ["installation_id"],
)

github_api_rate_limit_limit = Gauge(
    "stampbot_github_api_rate_limit_limit",
    "GitHub API rate limit ceiling",
    ["installation_id"],
)

# =============================================================================
# Repository Configuration Metrics
# =============================================================================

repo_config_loads_total = Counter(
    "stampbot_repo_config_loads_total",
    "Total repository configuration loads",
    ["status"],  # found, default, error
)

# =============================================================================
# Error Metrics
# =============================================================================

errors_total = Counter(
    "stampbot_errors_total",
    "Total errors by type",
    ["error_type"],  # signature_invalid, payload_invalid, approval_failed, dismiss_failed, etc.
)


def get_metrics() -> bytes:
    """Get metrics in Prometheus format.

    Returns:
        Prometheus metrics as UTF-8 encoded bytes.
    """
    return generate_latest(registry)


def set_app_info(version: str) -> None:
    """Set application info metric.

    Args:
        version: Application version string.
    """
    app_info.info({"version": version})
