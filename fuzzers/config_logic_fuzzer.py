# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Atheris fuzz target for repository approval eligibility logic."""

from __future__ import annotations

import re
import sys

import atheris

with atheris.instrument_imports(enable_loader_override=False):
    from stampbot.config import RepoConfig

MAX_INPUT_SIZE = 2048
MAX_FIELD_SIZE = 64
MAX_LIST_SIZE = 4


def _fields(data: bytes) -> list[str]:
    text = data[:MAX_INPUT_SIZE].decode("utf-8", errors="ignore")
    return [field[:MAX_FIELD_SIZE] for field in text.split("\0")]


def _slice(fields: list[str], start: int) -> list[str]:
    return [field for field in fields[start : start + MAX_LIST_SIZE] if field]


def TestOneInput(data: bytes) -> None:
    """Exercise bounded combinations of repo approval policy inputs."""
    fields = _fields(data)

    config = RepoConfig(
        approval_labels=_slice(fields, 0) or ["stamp"],
        auto_approve_on_label=bool(data[:1] and data[0] % 2),
        reapprove=bool(data[2:3] and data[2] % 2),
        chatops_enabled=bool(data[1:2] and data[1] % 2),
        chatops_required_permission="maintain",
        approve_commands=_slice(fields, 4) or ["stamp"],
        unapprove_commands=_slice(fields, 8) or ["unstamp"],
        required_labels=_slice(fields, 12),
        required_title_patterns=[re.escape(pattern) for pattern in _slice(fields, 16)],
        allowed_users=_slice(fields, 20),
        allowed_teams=_slice(fields, 24),
    )

    pr_labels = _slice(fields, 28)
    pr_title = fields[32] if len(fields) > 32 else ""
    pr_author = fields[33] if len(fields) > 33 else ""
    author_team_slugs = _slice(fields, 34)

    config.is_pr_eligible(pr_labels, pr_title, pr_author, author_team_slugs)
    config.needs_team_check(pr_author)


def main() -> None:
    """Run the fuzz target."""
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
