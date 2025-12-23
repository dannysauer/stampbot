"""Pytest configuration and fixtures."""

import json
import os
from pathlib import Path

# Set mock environment variables BEFORE any stampbot imports
# This ensures Settings() can be instantiated during test collection
os.environ.setdefault("STAMPBOT_APP_ID", "12345")
# Private key must start with -----BEGIN to be treated as key content (not file path)
# This is a mock RSA key for testing only - not a real key
os.environ.setdefault(
    "STAMPBOT_PRIVATE_KEY",
    """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MvDSgyCnP3k4WQiT
test-mock-key-data-for-unit-tests-only-not-a-real-key-do-not-use
-----END RSA PRIVATE KEY-----""",
)
os.environ.setdefault("STAMPBOT_WEBHOOK_SECRET", "mock-webhook-secret-for-testing")

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

# Path to fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file by name.

    Args:
        name: Fixture filename (with or without .json extension)

    Returns:
        Parsed JSON data as a dictionary
    """
    if not name.endswith(".json"):
        name = f"{name}.json"
    fixture_path = FIXTURES_DIR / name
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI app."""
    from stampbot.main import app

    return TestClient(app)


@pytest.fixture
def mock_github_client(monkeypatch):
    """Mock GitHub client for testing."""
    mock_client = Mock()
    monkeypatch.setattr("stampbot.github_client.github_client", mock_client)
    return mock_client


@pytest.fixture
def sample_pr_payload():
    """Sample pull request webhook payload."""
    return {
        "action": "opened",
        "number": 1,
        "pull_request": {
            "number": 1,
            "title": "Test PR",
            "state": "open",
            "labels": [{"name": "autoapprove"}],
            "base": {"ref": "main"},
        },
        "repository": {
            "full_name": "owner/repo",
            "name": "repo",
            "owner": {"login": "owner"},
        },
        "installation": {"id": 12345},
    }


@pytest.fixture
def sample_comment_payload():
    """Sample issue comment webhook payload."""
    return {
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
            "name": "repo",
            "owner": {"login": "owner"},
        },
        "installation": {"id": 12345},
    }
