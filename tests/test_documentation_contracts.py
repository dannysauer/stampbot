"""Keep operational documentation aligned with security-sensitive behavior."""

import re
from pathlib import Path


def _normalized(path: str) -> str:
    return re.sub(r"\s+", " ", Path(path).read_text(encoding="utf-8")).strip()


def test_policy_read_failure_boundaries_are_documented() -> None:
    required = {
        "docs/reference.md": (
            "repository-level `404`, so that response continues lookup to service defaults.",
            "A failure reading the target repository's policy stops automation for that event.",
            "Once GitHub makes the organization repository available to the App, "
            "a failure reading its policy does too.",
        ),
        "docs/architecture.md": (
            "Stampbot continues to service defaults in either case.",
            "A failure reading the target repository's policy stops automation for that event.",
            "Once GitHub makes the organization repository available to the App, "
            "a failure reading its policy does too.",
        ),
        "docs/operations.md": (
            "Stampbot treats that optional repository as unavailable and uses service defaults.",
            "A failure reading the target repository's policy stops automation for that event.",
            "Once GitHub makes the organization repository available to the App, "
            "a failure reading its policy does too.",
        ),
        "docs/configuration.md": (
            "GitHub returns a repository-level `404` and Stampbot uses service defaults.",
            "A failure reading the target repository's policy stops automation for that event.",
            "Once GitHub makes the organization repository available to the App, "
            "a failure reading its policy does too.",
        ),
        "docs/security-requirements.md": (
            "Stop automation when GitHub cannot complete a target-repository policy read.",
            "They also apply when the optional `OWNER/.github` repository doesn't "
            "exist or isn't part of the App installation.",
        ),
    }
    forbidden = (
        "read failure uses service defaults",
        "read failure also falls back to the service defaults",
        "read failure currently falls back to service defaults",
        "read failure may use service defaults",
    )

    for path, statements in required.items():
        documentation = _normalized(path)
        assert all(statement in documentation for statement in statements)
        assert all(old_statement not in documentation for old_statement in forbidden)
