import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release_plan = load_script("release_plan")


@pytest.mark.parametrize(
    ("commits", "chart_changed", "should_release", "should_release_chart", "version"),
    [
        ([release_plan.Commit("fix: repair webhook")], False, True, True, "1.2.4"),
        ([release_plan.Commit("docs: clarify install")], True, False, True, "1.2.3"),
        ([release_plan.Commit("fix: repair chart")], True, True, True, "1.2.4"),
        ([release_plan.Commit("docs: clarify install")], False, False, False, "1.2.3"),
    ],
    ids=["app-only", "chart-only", "combined", "neither"],
)
def test_release_ownership(
    commits: list[object],
    chart_changed: bool,
    should_release: bool,
    should_release_chart: bool,
    version: str,
) -> None:
    plan = release_plan.make_release_plan(commits, "v1.2.3", chart_changed)

    assert plan.should_release is should_release
    assert plan.should_release_chart is should_release_chart
    assert plan.version == version


def test_release_plan_uses_highest_required_bump() -> None:
    commits = [
        release_plan.Commit("feat: add mode"),
        release_plan.Commit("fix!: remove legacy mode"),
    ]

    plan = release_plan.make_release_plan(commits, "v1.2.3", False)

    assert plan.bump_type == "major"
    assert plan.version == "2.0.0"


def test_manual_release_owns_chart_release() -> None:
    plan = release_plan.make_release_plan([], "v1.2.3", False, requested_tag="v1.2.0")

    assert plan.should_release is True
    assert plan.should_release_chart is True
    assert plan.version == "1.2.0"


def test_latest_version_tag_uses_numeric_order() -> None:
    assert release_plan.latest_version_tag(["v1.9.9", "v1.10.0", "not-a-release"]) == "v1.10.0"


def test_latest_chart_tag_must_be_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_strict_tags(pattern: str, prefix: str, *, merged_only: bool = False) -> list[str]:
        assert pattern == "chart-v*"
        assert prefix == "chart-v"
        return ["chart-v1.2.2"] if merged_only else ["chart-v1.2.2", "chart-v1.2.3"]

    monkeypatch.setattr(release_plan, "strict_tags", fake_strict_tags)

    with pytest.raises(RuntimeError, match="is not an ancestor of HEAD"):
        release_plan.latest_chart_tag()


def test_requested_retry_tag_must_point_to_head(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--verify", "refs/tags/v1.2.3^{commit}"):
            return "older-commit\n"
        if args == ("rev-parse", "HEAD"):
            return "current-commit\n"
        raise AssertionError(f"unexpected git arguments: {args}")

    monkeypatch.setattr(release_plan, "git", fake_git)

    with pytest.raises(ValueError, match="must point to the checked-out commit"):
        release_plan.verify_requested_tag("v1.2.3")


def test_requested_retry_tag_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(*args: str) -> str:
        raise release_plan.subprocess.CalledProcessError(1, ["git", *args])

    monkeypatch.setattr(release_plan, "git", fake_git)

    with pytest.raises(ValueError, match="does not exist"):
        release_plan.verify_requested_tag("v1.2.3")


def test_requested_retry_tag_accepts_head(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(*args: str) -> str:
        if args in {
            ("rev-parse", "--verify", "refs/tags/v1.2.3^{commit}"),
            ("rev-parse", "HEAD"),
        }:
            return "current-commit\n"
        raise AssertionError(f"unexpected git arguments: {args}")

    monkeypatch.setattr(release_plan, "git", fake_git)

    release_plan.verify_requested_tag("v1.2.3")


def test_main_rejects_whitespace_padded_retry_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_plan.py",
            "--requested-tag",
            " v1.2.3 ",
            "--github-output",
            str(output),
        ],
    )

    with pytest.raises(ValueError, match="invalid application release tag"):
        release_plan.main()


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v01.2.3", "chart-v1.2.3"])
def test_parse_version_rejects_noncanonical_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="invalid application release tag"):
        release_plan.parse_version(tag)
