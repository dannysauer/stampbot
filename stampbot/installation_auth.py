# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Installation credentials whose token exchanges are serialized, traced, and counted."""

import threading
import time

from github import Auth
from github.InstallationAuthorization import InstallationAuthorization

from stampbot.metrics import github_api_request_duration_seconds, github_api_requests_total
from stampbot.telemetry import create_span, set_span_error, set_span_ok


class InstrumentedInstallationAuth(Auth.AppInstallationAuth):
    """Installation credentials shared by every thread working for one installation.

    PyGithub exchanges the App JWT for an installation token the first time
    ``token`` is read and again whenever the cached token is within its refresh
    threshold of expiry. Both exchanges run inside ``token``, which PyGithub also
    reads while signing every request, so this class wraps that property:

    - ``token`` takes a per-instance lock, so concurrent readers for one
      installation share a single exchange instead of each performing their own.
      Readers for other installations are never delayed.
    - ``_get_installation_authorization`` wraps PyGithub's exchange in a
      ``github.get_installation_token`` span and the ``get_token`` metrics, so a
      refresh is as visible as a first exchange. This overrides a private
      PyGithub method, which ``token`` calls for both cases in PyGithub 2.9.1,
      the version pinned in ``pyproject.toml``; ``tests/test_installation_auth.py``
      fails if an upgrade stops routing ``token`` through it.
    """

    def __init__(self, app_auth: Auth.AppAuth, installation_id: int) -> None:
        """Bind the App credentials for one installation.

        Args:
            app_auth: App credentials used to sign the JWT for the exchange.
            installation_id: GitHub App installation ID.
        """
        super().__init__(app_auth, installation_id)
        self._exchange_lock = threading.Lock()
        self._exchanged = False

    @property
    def token(self) -> str:
        """Return the installation token, exchanging or refreshing it under a lock.

        Raises:
            github.GithubException: If GitHub rejects an exchange.
        """
        # The lock is not reentrant. Holding it across the exchange is safe
        # because the exchange signs with the App JWT, never with this token,
        # so nothing inside it reads ``token`` again.
        with self._exchange_lock:
            return super().token

    def _get_installation_authorization(self) -> InstallationAuthorization:
        """Exchange the App JWT for an installation token, with a span and metrics.

        Returns:
            The new installation token and its expiry.

        Raises:
            github.GithubException: If GitHub rejects the exchange.
        """
        start_time = time.time()
        with create_span(
            "github.get_installation_token",
            {
                "github.installation_id": self.installation_id,
                "github.token_refresh": self._exchanged,
            },
        ) as span:
            try:
                authorization = super()._get_installation_authorization()
            except Exception as e:
                github_api_requests_total.labels(operation="get_token", status="failure").inc()
                set_span_error(span, e)
                raise
            else:
                github_api_requests_total.labels(operation="get_token", status="success").inc()
                set_span_ok(span)
                self._exchanged = True
                return authorization
            finally:
                github_api_request_duration_seconds.labels(operation="get_token").observe(
                    time.time() - start_time
                )
