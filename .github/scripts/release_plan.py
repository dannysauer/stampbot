#!/usr/bin/env python3
"""Calculate the application and chart work owned by the release workflow."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

SEMVER_TAG = re.compile(r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
RELEASABLE_SUBJECT = re.compile(r"^(?:feat|fix)(?:\(.+\))?(?:!)?:")
FEATURE_SUBJECT = re.compile(r"^feat(?:\(.+\))?:")
BREAKING_SUBJECT = re.compile(r"^[a-z]+(?:\(.+\))?!:")
BREAKING_BODY = re.compile(r"^BREAKING CHANGE:", re.MULTILINE)
git_path = shutil.which("git")
if git_path is None:
    raise RuntimeError("git is required to calculate a release plan")
GIT: str = git_path


@dataclass(frozen=True)
class Commit:
    """Commit text used for semantic-version decisions."""

    subject: str
    body: str = ""


@dataclass(frozen=True)
class ReleasePlan:
    """Release work to perform for the checked-out commit."""

    version: str
    should_release: bool
    should_release_chart: bool
    bump_type: str | None


def parse_version(tag: str) -> tuple[int, int, int]:
    """Return the numeric components of a strict application release tag."""
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        raise ValueError(f"invalid application release tag: {tag}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def latest_version_tag(tags: list[str]) -> str | None:
    """Select the highest strict semantic-version application tag."""
    valid = [(parse_version(tag), tag) for tag in tags if SEMVER_TAG.fullmatch(tag)]
    return max(valid)[1] if valid else None


def bump_version(tag: str | None, bump_type: str) -> str:
    """Apply a conventional-commit bump to an application version tag."""
    major, minor, patch = parse_version(tag or "v0.0.0")
    if bump_type == "major":
        return f"{major + 1}.0.0"
    if bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    if bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unsupported bump type: {bump_type}")


def make_release_plan(
    commits: list[Commit],
    latest_app_tag: str | None,
    chart_changed: bool,
    requested_tag: str | None = None,
) -> ReleasePlan:
    """Plan app and chart ownership from commits and chart changes."""
    if requested_tag:
        version = ".".join(str(part) for part in parse_version(requested_tag))
        return ReleasePlan(
            version=version,
            should_release=True,
            should_release_chart=True,
            bump_type=None,
        )

    releasable = any(RELEASABLE_SUBJECT.match(commit.subject) for commit in commits)
    if not releasable:
        current_version = latest_app_tag.removeprefix("v") if latest_app_tag else ""
        return ReleasePlan(
            version=current_version,
            should_release=False,
            should_release_chart=chart_changed,
            bump_type=None,
        )

    if any(
        BREAKING_SUBJECT.match(commit.subject) or BREAKING_BODY.search(commit.body)
        for commit in commits
    ):
        bump_type = "major"
    elif any(FEATURE_SUBJECT.match(commit.subject) for commit in commits):
        bump_type = "minor"
    else:
        bump_type = "patch"

    return ReleasePlan(
        version=bump_version(latest_app_tag, bump_type),
        should_release=True,
        should_release_chart=True,
        bump_type=bump_type,
    )


def git(*args: str) -> str:
    """Run a read-only Git query and return its standard output."""
    result = subprocess.run(  # noqa: S603 - arguments are constructed by this script.
        [GIT, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def commit_range(latest_tag: str | None) -> str:
    """Return the Git revision range containing unreleased application commits."""
    return f"refs/tags/{latest_tag}..HEAD" if latest_tag else "HEAD"


def read_commits(revision_range: str) -> list[Commit]:
    """Read subjects and bodies without relying on line-oriented delimiters."""
    raw = git("log", revision_range, "--format=%x1e%s%x1f%b")
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        if not record:
            continue
        subject, separator, body = record.partition("\x1f")
        if separator:
            commits.append(Commit(subject=subject.strip(), body=body.strip()))
    return commits


def strict_tags(pattern: str, prefix: str, *, merged_only: bool = False) -> list[str]:
    """Read numeric semantic-version tags with the requested prefix."""
    numeric = re.compile(rf"^{re.escape(prefix)}(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
    command = ["tag"]
    if merged_only:
        command.extend(["--merged", "HEAD"])
    command.extend(["--list", pattern])
    tags = git(*command).splitlines()
    return [tag for tag in tags if numeric.fullmatch(tag)]


def latest_chart_tag() -> str | None:
    """Return the highest strict chart tag, requiring it to be reachable."""
    tags = strict_tags("chart-v*", "chart-v")
    latest = max(tags, key=lambda tag: parse_version(tag.removeprefix("chart-"))) if tags else None
    reachable = set(strict_tags("chart-v*", "chart-v", merged_only=True))
    if latest and latest not in reachable:
        raise RuntimeError(f"latest chart tag {latest} is not an ancestor of HEAD")
    return latest


def charts_changed_since(tag: str | None) -> bool:
    """Report whether the current tree has chart changes after a chart tag."""
    if tag:
        changed = git("diff", "--name-only", f"refs/tags/{tag}..HEAD", "--", "charts/")
    else:
        changed = git("ls-tree", "-r", "--name-only", "HEAD", "--", "charts/")
    return bool(changed.strip())


def verify_requested_tag(tag: str) -> None:
    """Require a strict existing application tag for the checked-out commit."""
    parse_version(tag)
    try:
        tag_commit = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()
    except subprocess.CalledProcessError as error:
        raise ValueError(f"requested retry tag does not exist: {tag}") from error

    head_commit = git("rev-parse", "HEAD").strip()
    if tag_commit != head_commit:
        raise ValueError("requested retry tag must point to the checked-out commit")


def write_output(output: TextIO, name: str, value: str | bool) -> None:
    """Write a scalar GitHub Actions output."""
    rendered = str(value).lower() if isinstance(value, bool) else value
    output.write(f"{name}={rendered}\n")


def main() -> None:
    """Calculate and emit the release plan for GitHub Actions."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--requested-tag", default="")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()

    requested_tag = args.requested_tag or None
    if requested_tag:
        verify_requested_tag(requested_tag)

    app_tags = strict_tags("v*", "v")
    latest_app_tag = latest_version_tag(app_tags)
    reachable_app_tags = set(strict_tags("v*", "v", merged_only=True))
    if latest_app_tag and latest_app_tag not in reachable_app_tags:
        raise RuntimeError(f"latest application tag {latest_app_tag} is not an ancestor of HEAD")
    commits = read_commits(commit_range(latest_app_tag))
    chart_tag = latest_chart_tag()
    chart_changed = charts_changed_since(chart_tag)
    plan = make_release_plan(commits, latest_app_tag, chart_changed, requested_tag)

    print(f"Latest application tag: {latest_app_tag or 'none'}")
    print(f"Latest chart tag: {chart_tag or 'none'}")
    print(f"Application release required: {plan.should_release}")
    print(f"Chart release required: {plan.should_release_chart}")
    if plan.bump_type:
        print(f"Application version bump: {plan.bump_type}")
    if plan.version:
        print(f"Application version: {plan.version}")

    with args.github_output.open("a", encoding="utf-8") as output:
        write_output(output, "version", plan.version)
        write_output(output, "should_release", plan.should_release)
        write_output(output, "should_release_chart", plan.should_release_chart)


if __name__ == "__main__":
    main()
