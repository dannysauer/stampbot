# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Atheris fuzz target for stampbot.toml parsing."""

from __future__ import annotations

import sys

import atheris
import toml

with atheris.instrument_imports(enable_loader_override=False):
    from stampbot.config import RepoConfig

MAX_INPUT_SIZE = 4096
EXPECTED_CONFIG_EXCEPTIONS = (TypeError, ValueError, toml.TomlDecodeError)


def TestOneInput(data: bytes) -> None:
    """Parse arbitrary bounded TOML-like input as repository config."""
    toml_content = data[:MAX_INPUT_SIZE].decode("utf-8", errors="ignore")

    try:
        RepoConfig.from_toml(toml_content)
    except EXPECTED_CONFIG_EXCEPTIONS:
        return


def main() -> None:
    """Run the fuzz target."""
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
