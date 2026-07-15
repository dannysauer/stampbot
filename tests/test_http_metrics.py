"""Tests for HTTP metric label behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from stampbot.main import UNMATCHED_ENDPOINT, _metric_endpoint_label, metrics_middleware

HTTP_METRIC_NAMES = (
    "http_requests_total",
    "http_request_duration_seconds",
    "http_request_size_bytes",
    "http_response_size_bytes",
    "http_requests_in_progress",
)


@pytest.fixture
def http_metric_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace HTTP metrics with isolated spies."""
    spies: dict[str, MagicMock] = {}
    for name in HTTP_METRIC_NAMES:
        metric = MagicMock(name=name)
        monkeypatch.setattr(f"stampbot.main.{name}", metric)
        spies[name] = metric
    return spies


@pytest.fixture
def routed_app() -> FastAPI:
    """Create an app with dynamic, method-specific, and failing routes."""
    app = FastAPI()
    app.middleware("http")(metrics_middleware)

    @app.get("/widgets/{widget_id}")
    async def get_widget(widget_id: str) -> dict[str, str]:
        return {"widget_id": widget_id}

    @app.post("/method-specific")
    async def method_specific() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("test failure")

    return app


def _endpoint_labels(metric: MagicMock) -> list[str]:
    """Extract endpoint labels passed to a mocked metric."""
    return [record.kwargs["endpoint"] for record in metric.labels.call_args_list]


def test_existing_scope_route_uses_its_template() -> None:
    """A route resolved by an outer router is used without rematching."""
    request = Request(
        {
            "type": "http",
            "route": SimpleNamespace(path="/mounted/{resource_id}"),
        }
    )

    assert _metric_endpoint_label(request) == "/mounted/{resource_id}"


def test_route_without_a_path_uses_bounded_fallback() -> None:
    """Custom framework routes without path templates cannot leak raw paths."""
    route = MagicMock()
    route.matches.return_value = (object(), {"route": SimpleNamespace(path=None)})
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(routes=[route]),
            "path": "/raw-user-controlled-path",
        }
    )

    assert _metric_endpoint_label(request) == UNMATCHED_ENDPOINT


def test_unknown_paths_share_one_bounded_label(
    test_client: TestClient,
    http_metric_spies: dict[str, MagicMock],
) -> None:
    """Arbitrary 404 paths must not create attacker-controlled label values."""
    unknown_paths = (
        "/not-a-route/attacker-value-one",
        "/another/made-up/path/attacker-value-two",
    )

    for path in unknown_paths:
        response = test_client.post(path, content=b"request body")
        assert response.status_code == 404

    assert _endpoint_labels(http_metric_spies["http_requests_total"]) == [
        UNMATCHED_ENDPOINT,
        UNMATCHED_ENDPOINT,
    ]
    for metric in http_metric_spies.values():
        assert all(label == UNMATCHED_ENDPOINT for label in _endpoint_labels(metric))


def test_exported_metrics_never_contain_an_unknown_raw_path(test_client: TestClient) -> None:
    """The public metrics payload exposes only the bounded fallback for a 404."""
    raw_path = "/unknown/security-regression-canary-8f3810"

    assert test_client.get(raw_path).status_code == 404
    metrics_response = test_client.get("/metrics")

    assert metrics_response.status_code == 200
    assert 'endpoint="unmatched"' in metrics_response.text
    assert raw_path not in metrics_response.text


def test_dynamic_path_uses_route_template(
    routed_app: FastAPI,
    http_metric_spies: dict[str, MagicMock],
) -> None:
    """Path parameters are represented by their stable route template."""
    client = TestClient(routed_app)

    response = client.get("/widgets/user-controlled-123")

    assert response.status_code == 200
    assert _endpoint_labels(http_metric_spies["http_requests_total"]) == ["/widgets/{widget_id}"]
    for metric in http_metric_spies.values():
        assert "user-controlled-123" not in _endpoint_labels(metric)


def test_setup_routes_keep_distinct_static_labels(
    test_client: TestClient,
    http_metric_spies: dict[str, MagicMock],
) -> None:
    """Setup and callback traffic remains distinguishable by route."""
    setup_response = test_client.get("/setup")
    callback_response = test_client.get("/setup/callback")

    assert setup_response.status_code == 200
    assert callback_response.status_code == 422
    assert _endpoint_labels(http_metric_spies["http_requests_total"]) == [
        "/setup",
        "/setup/callback",
    ]


def test_method_not_allowed_uses_matching_route_template(
    routed_app: FastAPI,
    http_metric_spies: dict[str, MagicMock],
) -> None:
    """A 405 remains attributable to the bounded matching route."""
    client = TestClient(routed_app)

    response = client.get("/method-specific")

    assert response.status_code == 405
    http_metric_spies["http_requests_total"].labels.assert_called_once_with(
        method="GET",
        endpoint="/method-specific",
        status=405,
    )


def test_exception_still_clears_in_progress_metric(
    routed_app: FastAPI,
    http_metric_spies: dict[str, MagicMock],
) -> None:
    """Unhandled endpoint exceptions must not leave the in-progress gauge raised."""
    client = TestClient(routed_app, raise_server_exceptions=False)

    response = client.get("/explode")

    assert response.status_code == 500
    gauge = http_metric_spies["http_requests_in_progress"]
    assert gauge.labels.call_args_list == [
        call(method="GET", endpoint="/explode"),
        call(method="GET", endpoint="/explode"),
    ]
    gauge.labels.return_value.inc.assert_called_once_with()
    gauge.labels.return_value.dec.assert_called_once_with()
    http_metric_spies["http_requests_total"].labels.assert_not_called()
