# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Resolve the Stampbot version exposed by the running application."""

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

_DISTRIBUTION_NAME = "stampbot"
_VERSION_ENVIRONMENT_VARIABLE = "STAMPBOT_VERSION"
_UNKNOWN_VERSION = "0.0.0+unknown"


def _resolve_app_version() -> str:
    """Return build-injected, installed-package, or unknown version metadata."""
    injected_version = os.getenv(_VERSION_ENVIRONMENT_VARIABLE, "").strip()
    if injected_version:
        return injected_version

    try:
        installed_version = distribution_version(_DISTRIBUTION_NAME).strip()
    except PackageNotFoundError:
        return _UNKNOWN_VERSION

    return installed_version or _UNKNOWN_VERSION


APP_VERSION = _resolve_app_version()
