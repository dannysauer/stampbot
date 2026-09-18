"""Tests for the image-contents check that CI runs inside the built image."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check_image_contents.py"


@pytest.fixture(scope="module")
def check() -> ModuleType:
    """Load the script as a module; ``scripts/`` is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("check_image_contents", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _distribution(name: str, version: str) -> SimpleNamespace:
    return SimpleNamespace(metadata={"Name": name}, version=version)


REQUIREMENTS = """\
annotated-doc==0.0.4 ; python_version >= "3.11" and python_version < "3.15" \\
    --hash=sha256:placeholder \\
    --hash=sha256:placeholder
PyGithub==2.9.1 ; python_version >= "3.11" and python_version < "3.15" \\
    --hash=sha256:placeholder
typing_extensions==4.15.0 \\
    --hash=sha256:placeholder
nowhere-only==1.0 ; platform_system == "NoSuchOS" \\
    --hash=sha256:placeholder
everywhere==2.0 ; python_version >= "3" \\
    --hash=sha256:placeholder
"""


def test_normalize_follows_pep_503(check: ModuleType) -> None:
    assert check.normalize("PyGithub") == "pygithub"
    assert check.normalize("typing_extensions") == "typing-extensions"
    assert check.normalize("zope.interface") == "zope-interface"
    assert check.normalize("a__b--c..d") == "a-b-c-d"


def test_declared_reads_pins_and_evaluates_markers(check: ModuleType) -> None:
    """A pin pip would skip for this interpreter is not expected in the image."""
    assert check.declared(REQUIREMENTS) == {
        "annotated-doc": "0.0.4",
        "pygithub": "2.9.1",
        "typing-extensions": "4.15.0",
        "everywhere": "2.0",
    }


def test_marker_applies_assumes_yes_without_packaging(
    check: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under an interpreter without ``packaging`` a gated pin is still expected."""
    monkeypatch.setitem(sys.modules, "packaging.markers", None)
    assert check.marker_applies('platform_system == "NoSuchOS"') is True
    assert check.marker_applies(None) is True


def test_installed_surfaces_a_distribution_without_a_name(check: ModuleType) -> None:
    dist = SimpleNamespace(metadata={}, version="0.1", _path=Path("broken-0.1.dist-info"))
    found = check.installed([dist])
    assert list(found.values()) == ["0.1"]
    assert next(iter(found)).startswith("unnamed-")


def test_declared_ignores_comments_and_blank_lines(check: ModuleType) -> None:
    text = "# exported by poetry\n\nrequests==2.32.0\n    --hash=sha256:aa\n"
    assert check.declared(text) == {"requests": "2.32.0"}


def test_installed_normalizes_names(check: ModuleType) -> None:
    dists = [_distribution("PyGithub", "2.9.1"), _distribution("typing_extensions", "4.15.0")]
    assert check.installed(dists) == {"pygithub": "2.9.1", "typing-extensions": "4.15.0"}


def test_compare_reports_nothing_when_sets_match(check: ModuleType) -> None:
    pins = {"a": "1", "b": "2"}
    assert check.compare(pins, dict(pins)) == []


def test_compare_names_extra_missing_and_changed(check: ModuleType) -> None:
    expected = {"kept": "1.0", "missing": "2.0", "changed": "3.0"}
    actual = {"kept": "1.0", "changed": "3.1", "setuptools": "70.3.0", "pip": "26.2.1"}

    assert check.compare(expected, actual) == [
        "not declared in requirements.txt: pip==26.2.1",
        "not declared in requirements.txt: setuptools==70.3.0",
        "declared but not installed: missing==2.0",
        "version differs: changed declared 3.0, installed 3.1",
    ]


def test_main_succeeds_when_image_matches(
    check: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(REQUIREMENTS, encoding="utf-8")
    monkeypatch.setattr(
        check,
        "installed",
        lambda: {
            "annotated-doc": "0.0.4",
            "pygithub": "2.9.1",
            "typing-extensions": "4.15.0",
            "everywhere": "2.0",
        },
    )

    assert check.main([str(requirements)]) == 0
    assert "exactly the 4 distributions" in capsys.readouterr().out


def test_main_fails_and_lists_every_problem(
    check: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(REQUIREMENTS, encoding="utf-8")
    monkeypatch.setattr(
        check,
        "installed",
        lambda: {
            "annotated-doc": "0.0.4",
            "pygithub": "2.9.1",
            "everywhere": "2.0",
            "pip": "26.2.1",
        },
    )

    assert check.main([str(requirements)]) == 1
    err = capsys.readouterr().err
    assert "not declared in requirements.txt: pip==26.2.1" in err
    assert "declared but not installed: typing-extensions==4.15.0" in err


def test_installed_reads_the_running_interpreter_by_default(check: ModuleType) -> None:
    """Without an explicit list the script inspects the interpreter it runs in."""
    found = check.installed()
    assert "pytest" in found
