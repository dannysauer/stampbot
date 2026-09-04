# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Tests for the serialized, traced, and counted installation token exchange."""

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from github import Auth, Github
from github.GithubException import GithubException

from stampbot.installation_auth import InstrumentedInstallationAuth
from stampbot.metrics import github_api_request_duration_seconds, github_api_requests_total

# pragma: allowlist nextline secret
TEST_PEM_KEY = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
TEST_TOKEN = "test-token"  # noqa: S105
EXCHANGE = "_get_installation_authorization"


def _counter(status: str) -> float:
    return github_api_requests_total.labels(operation="get_token", status=status)._value.get()


def _duration_count() -> float:
    histogram = github_api_request_duration_seconds.labels(operation="get_token")
    return next(sample.value for sample in histogram._samples() if sample.name.endswith("_count"))


def _authorization(*, expired: bool) -> Mock:
    """Fake PyGithub authorization; an expired one makes the next ``token`` read refresh."""
    offset = timedelta(minutes=-1) if expired else timedelta(hours=1)
    return Mock(token=TEST_TOKEN, expires_at=datetime.now(UTC) + offset)


@pytest.fixture
def installation_auth() -> InstrumentedInstallationAuth:
    """Real credentials bound to a real App auth and requester, as in production."""
    auth = InstrumentedInstallationAuth(Auth.AppAuth(12345, TEST_PEM_KEY), 777)
    Github(auth=auth, lazy=True)
    return auth


@pytest.fixture
def span():
    """A ``create_span`` stand-in that records the span name and attributes."""
    with patch("stampbot.installation_auth.create_span") as mock_span:
        mock_span.return_value.__enter__ = Mock(return_value=None)
        mock_span.return_value.__exit__ = Mock(return_value=False)
        yield mock_span


class TestInstrumentedInstallationAuth:
    """Every token exchange, first or refresh, is one span and one metric increment."""

    def test_token_exchanges_once_and_refreshes_when_expired(self, installation_auth, span):
        """Test PyGithub's ``token`` routes first exchange and refresh through the override."""
        successes = _counter("success")
        durations = _duration_count()
        with patch.object(
            Auth.AppInstallationAuth,
            EXCHANGE,
            side_effect=[_authorization(expired=True), _authorization(expired=False)],
        ) as exchange:
            assert installation_auth.token == TEST_TOKEN  # first exchange
            assert installation_auth.token == TEST_TOKEN  # expired, so refresh
            assert installation_auth.token == TEST_TOKEN  # fresh, no exchange

        assert exchange.call_count == 2
        assert _counter("success") == successes + 2
        assert _duration_count() == durations + 2
        assert [c.args for c in span.call_args_list] == [
            (
                "github.get_installation_token",
                {"github.installation_id": 777, "github.token_refresh": False},
            ),
            (
                "github.get_installation_token",
                {"github.installation_id": 777, "github.token_refresh": True},
            ),
        ]

    def test_failed_exchange_is_counted_and_raised(self, installation_auth, span):
        """Test a rejected exchange increments the failure counter and propagates."""
        failure = GithubException(401, {"message": "Bad credentials"}, None)
        failures = _counter("failure")
        durations = _duration_count()
        with patch.object(Auth.AppInstallationAuth, EXCHANGE, side_effect=failure):
            with pytest.raises(GithubException) as error:
                installation_auth.token  # noqa: B018

        assert error.value is failure
        assert _counter("failure") == failures + 1
        assert _duration_count() == durations + 1
        assert not installation_auth._exchange_lock.locked()

        # PyGithub keeps no authorization after a failure, so the next read
        # retries, and it is still a first exchange rather than a refresh.
        with patch.object(
            Auth.AppInstallationAuth, EXCHANGE, return_value=_authorization(expired=False)
        ):
            assert installation_auth.token == TEST_TOKEN
        assert [c.args[1]["github.token_refresh"] for c in span.call_args_list] == [False, False]

    def test_concurrent_readers_share_one_exchange(self, installation_auth, span):
        """Test threads reading ``token`` together wait for one exchange instead of racing."""
        release = threading.Event()
        started = threading.Event()

        def slow_exchange():
            started.set()
            release.wait(timeout=5)
            return _authorization(expired=False)

        tokens = []
        with patch.object(Auth.AppInstallationAuth, EXCHANGE, side_effect=slow_exchange) as ex:
            threads = [
                threading.Thread(target=lambda: tokens.append(installation_auth.token))
                for _ in range(3)
            ]
            for thread in threads:
                thread.start()
            assert started.wait(timeout=5)
            # The exchange is in progress and holds the lock; nobody else exchanges.
            assert installation_auth._exchange_lock.locked()
            release.set()
            for thread in threads:
                thread.join(timeout=5)

        assert tokens == [TEST_TOKEN] * 3
        assert ex.call_count == 1


class TestGitHubAppClientWiring:
    """The client caches the real credential class and exchanges through it."""

    def test_client_caches_real_credentials_and_exchanges_once(self, span):
        """Test two operations for one installation share one exchange end to end."""
        from stampbot.github_client import GitHubAppClient

        successes = _counter("success")
        with (
            patch("stampbot.github_client.is_configured", return_value=True),
            patch("stampbot.github_client.settings") as mock_settings,
            patch.object(
                Auth.AppInstallationAuth,
                EXCHANGE,
                return_value=_authorization(expired=False),
            ) as exchange,
        ):
            mock_settings.app_id = 12345
            mock_settings.private_key = TEST_PEM_KEY

            client = GitHubAppClient()
            first = client._get_installation_client(42)
            second = client._get_installation_client(42)

        cached = client._installation_auths[42]
        assert isinstance(cached, InstrumentedInstallationAuth)
        assert first is not second
        assert first.requester.auth is cached  # both clients sign with the shared credentials
        assert exchange.call_count == 1
        assert _counter("success") == successes + 1
        assert span.call_count == 1
        assert not cached._exchange_lock.locked()
