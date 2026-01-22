# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point for stampbot."""

import uvicorn

from stampbot.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "stampbot.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
