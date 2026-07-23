"""Keep operational documentation aligned with security-sensitive behavior."""

import re
from pathlib import Path


def _normalized(path: str) -> str:
    return re.sub(r"\s+", " ", Path(path).read_text(encoding="utf-8")).strip()


def test_policy_read_failures_are_documented_as_fail_closed() -> None:
    required = {
        "docs/reference.md": (
            "A GitHub read failure or a readable but invalid file stops automation for that event."
        ),
        "docs/architecture.md": (
            "If GitHub cannot complete a policy read, or if a readable policy is "
            "invalid, Stampbot stops automation for that event."
        ),
        "docs/operations.md": (
            "If GitHub cannot complete a policy read, or if a readable file is "
            "invalid, Stampbot records a policy-load error and stops automation "
            "for that event."
        ),
        "docs/configuration.md": (
            "A GitHub read failure or readable but invalid policy fails closed for that event"
        ),
        "docs/security-requirements.md": (
            "Stop automation when GitHub cannot complete a policy read."
        ),
    }
    forbidden = (
        "read failure uses service defaults",
        "read failure also falls back to the service defaults",
        "read failure currently falls back to service defaults",
        "read failure may use service defaults",
    )

    for path, statement in required.items():
        documentation = _normalized(path)
        assert statement in documentation
        assert all(old_statement not in documentation for old_statement in forbidden)
