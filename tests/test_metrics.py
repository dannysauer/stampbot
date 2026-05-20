# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Tests for Prometheus metric definitions."""

import math

from prometheus_client import Counter, Gauge, Histogram, Info

from stampbot import metrics


def test_histogram_contracts():
    """Test histogram names, labels, and bucket boundaries."""
    expected_histograms = {
        "http_request_duration_seconds": {
            "name": "stampbot_http_request_duration_seconds",
            "labels": ("method", "endpoint"),
            "buckets": (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        },
        "http_request_size_bytes": {
            "name": "stampbot_http_request_size_bytes",
            "labels": ("method", "endpoint"),
            "buckets": (100, 500, 1000, 5000, 10000, 50000, 100000, 500000),
        },
        "http_response_size_bytes": {
            "name": "stampbot_http_response_size_bytes",
            "labels": ("method", "endpoint"),
            "buckets": (100, 500, 1000, 5000, 10000, 50000, 100000, 500000),
        },
        "webhook_processing_duration_seconds": {
            "name": "stampbot_webhook_processing_duration_seconds",
            "labels": ("event_type",),
            "buckets": (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        },
        "pr_approval_duration_seconds": {
            "name": "stampbot_pr_approval_duration_seconds",
            "labels": (),
            "buckets": (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        },
        "pr_dismissal_duration_seconds": {
            "name": "stampbot_pr_dismissal_duration_seconds",
            "labels": (),
            "buckets": (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        },
        "github_api_request_duration_seconds": {
            "name": "stampbot_github_api_request_duration_seconds",
            "labels": ("operation",),
            "buckets": (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        },
    }

    for attribute, expected in expected_histograms.items():
        histogram = getattr(metrics, attribute)

        assert isinstance(histogram, Histogram)
        assert histogram._name == expected["name"]
        assert histogram._labelnames == expected["labels"]
        assert histogram._upper_bounds == [*expected["buckets"], math.inf]


def test_counter_and_gauge_contracts():
    """Test counter and gauge names and labels."""
    expected_metrics = {
        "http_requests_total": (
            Counter,
            "stampbot_http_requests",
            ("method", "endpoint", "status"),
        ),
        "webhook_events_total": (Counter, "stampbot_webhook_events", ("event_type", "action")),
        "webhook_signature_validations_total": (
            Counter,
            "stampbot_webhook_signature_validations",
            ("result",),
        ),
        "pr_approvals_total": (Counter, "stampbot_pr_approvals", ("trigger_type", "status")),
        "pr_dismissals_total": (Counter, "stampbot_pr_dismissals", ("trigger_type", "status")),
        "chatops_commands_total": (Counter, "stampbot_chatops_commands", ("command", "status")),
        "github_api_requests_total": (
            Counter,
            "stampbot_github_api_requests",
            ("operation", "status"),
        ),
        "repo_config_loads_total": (Counter, "stampbot_repo_config_loads", ("status",)),
        "errors_total": (Counter, "stampbot_errors", ("error_type",)),
        "http_requests_in_progress": (
            Gauge,
            "stampbot_http_requests_in_progress",
            ("method", "endpoint"),
        ),
        "github_api_rate_limit_remaining": (
            Gauge,
            "stampbot_github_api_rate_limit_remaining",
            ("installation_id",),
        ),
        "github_api_rate_limit_limit": (
            Gauge,
            "stampbot_github_api_rate_limit_limit",
            ("installation_id",),
        ),
    }

    for attribute, (metric_type, name, labels) in expected_metrics.items():
        metric = getattr(metrics, attribute)

        assert isinstance(metric, metric_type)
        assert metric._name == name
        assert metric._labelnames == labels


def test_app_info_contract():
    """Test the app info metric name."""
    assert isinstance(metrics.app_info, Info)
    assert metrics.app_info._name == "stampbot"
