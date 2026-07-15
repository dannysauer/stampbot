"""Tests for application version resolution."""

from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

from stampbot.version import APP_VERSION, _resolve_app_version


def test_injected_version_takes_precedence(monkeypatch):
    monkeypatch.setenv("STAMPBOT_VERSION", " 2.3.4 ")

    with patch("stampbot.version.distribution_version") as distribution_version:
        assert _resolve_app_version() == "2.3.4"

    distribution_version.assert_not_called()


def test_installed_distribution_is_development_fallback(monkeypatch):
    monkeypatch.delenv("STAMPBOT_VERSION", raising=False)

    with patch("stampbot.version.distribution_version", return_value="1.2.3"):
        assert _resolve_app_version() == "1.2.3"


def test_unknown_fallback_without_build_or_distribution_version(monkeypatch):
    monkeypatch.setenv("STAMPBOT_VERSION", "   ")

    with patch(
        "stampbot.version.distribution_version",
        side_effect=PackageNotFoundError,
    ):
        assert _resolve_app_version() == "0.0.0+unknown"


def test_package_version_matches_resolved_app_version():
    from stampbot import __version__

    assert __version__ == APP_VERSION
