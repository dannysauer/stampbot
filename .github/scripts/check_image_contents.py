#!/usr/bin/env python3
"""Compare the distributions installed in this interpreter with ``requirements.txt``.

Run this inside the container image, with the repository mounted read-only::

    docker run --rm --volume "$PWD:/src:ro" IMAGE \
        python -B /src/.github/scripts/check_image_contents.py /src/requirements.txt

``requirements.txt`` is exported from ``pyproject.toml`` and pins every
dependency, direct and transitive, so it is the allowlist of what the image
may contain. This reports anything installed that the file does not declare,
anything declared that is not installed, and any version that differs, then
exits non-zero if it found any of the three.

A pin whose environment marker does not apply to the running interpreter is
skipped, because pip skipped it too. Markers are evaluated with ``packaging``
when it is importable, which the venv always can because ``packaging`` is a
declared dependency; anywhere else the pin is assumed to apply, which errs
towards reporting something missing rather than hiding it. Everything else is
standard library, so the script also runs under the base interpreter, which
ships no installer and is expected to hold no distributions at all.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Mapping
from importlib import metadata
from pathlib import Path

# A pinned requirement line: ``name==version``, an optional ``; marker``, and a
# line continuation. Hash lines start with whitespace and never match.
_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)"  # name==version
    r"(?:\s*;\s*([^\\]*?))?"  # optional environment marker
    r"\s*\\?\s*$"  # optional continuation backslash
)


def normalize(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def marker_applies(marker: str | None) -> bool:
    """Return whether an environment marker selects the running interpreter."""
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ImportError:
        return True
    return Marker(marker).evaluate()


def declared(requirements: str) -> dict[str, str]:
    """Return ``{normalized name: version}`` for every applicable pin in a requirements file."""
    pins: dict[str, str] = {}
    for line in requirements.splitlines():
        match = _REQUIREMENT.match(line)
        if match and marker_applies(match.group(3)):
            pins[normalize(match.group(1))] = match.group(2)
    return pins


def installed(distributions: Iterable[metadata.Distribution] | None = None) -> dict[str, str]:
    """Return ``{normalized name: version}`` for every installed distribution."""
    if distributions is None:
        distributions = metadata.distributions()
    found: dict[str, str] = {}
    for dist in distributions:
        # A dist-info without a Name is malformed; surface it rather than crash.
        name = dist.metadata.get("Name") or f"unnamed-{getattr(dist, '_path', 'distribution')}"
        found[normalize(str(name))] = dist.version
    return found


def compare(expected: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    """Return one line per difference between the declared and installed sets."""
    problems = [
        f"not declared in requirements.txt: {name}=={actual[name]}"
        for name in sorted(set(actual) - set(expected))
    ]
    problems.extend(
        f"declared but not installed: {name}=={expected[name]}"
        for name in sorted(set(expected) - set(actual))
    )
    problems.extend(
        f"version differs: {name} declared {expected[name]}, installed {actual[name]}"
        for name in sorted(set(expected) & set(actual))
        if expected[name] != actual[name]
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Compare the running interpreter's distributions with a requirements file."""
    parser = argparse.ArgumentParser(
        description="Compare installed distributions with requirements.txt."
    )
    parser.add_argument("requirements", type=Path, help="path to the exported requirements.txt")
    args = parser.parse_args(argv)

    expected = declared(args.requirements.read_text(encoding="utf-8"))
    problems = compare(expected, installed())
    if problems:
        print("image contents do not match requirements.txt:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"image contains exactly the {len(expected)} distributions requirements.txt declares")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
