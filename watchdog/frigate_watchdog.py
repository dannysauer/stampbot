#!/usr/bin/env python3
# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Frigate NVR watchdog.

Polls the Frigate stats API and restarts the container when all cameras
report zero capture_fps for longer than STUCK_THRESHOLD seconds.  This
catches the condition where the Frigate process is alive but has silently
stopped reading from cameras (and therefore stopped recording).

Configuration (environment variables):
    FRIGATE_URL         Base URL of the Frigate API.  Default: http://frigate:5000
    FRIGATE_CONTAINER   Name or ID of the Frigate container.  Default: frigate
    CHECK_INTERVAL      Seconds between health polls.  Default: 60
    STUCK_THRESHOLD     Seconds of zero capture_fps before a restart is triggered.
                        Default: 300
"""

import logging
import os
import sys
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import docker
import requests

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


@dataclass
class Config:
    """Watchdog configuration loaded from environment variables."""

    frigate_url: str = field(
        default_factory=lambda: os.environ.get("FRIGATE_URL", "http://frigate:5000")
    )
    container_name: str = field(
        default_factory=lambda: os.environ.get("FRIGATE_CONTAINER", "frigate")
    )
    check_interval: int = field(
        default_factory=lambda: int(os.environ.get("CHECK_INTERVAL", "60"))
    )
    stuck_threshold: int = field(
        default_factory=lambda: int(os.environ.get("STUCK_THRESHOLD", "300"))
    )


def _fetch_stats(frigate_url: str) -> Optional[dict]:
    """Return parsed JSON from /api/stats, or None on any failure."""
    try:
        response = requests.get(f"{frigate_url}/api/stats", timeout=10)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not reach Frigate API: %s", exc)
        return None


def _is_capturing(stats: dict) -> bool:
    """Return True when at least one camera is actively capturing frames.

    A capture_fps of 0 on every camera means Frigate has stopped reading
    from the video sources even though the process is still alive.
    When no cameras are configured yet we return True to avoid a spurious
    restart on a fresh or partially-configured instance.
    """
    cameras: dict = stats.get("cameras", {})
    if not cameras:
        return True
    return any(cam.get("capture_fps", 0) > 0 for cam in cameras.values())


def _restart_container(container_name: str) -> None:
    """Send a restart to the named Docker container via the local socket."""
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        log.warning("Sending restart to container '%s'", container_name)
        container.restart(timeout=30)
        log.info("Restart command accepted by Docker daemon")
    except docker.errors.NotFound:
        log.error("Container '%s' not found — check FRIGATE_CONTAINER", container_name)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to restart container: %s", exc)


def run(config: Config) -> None:
    """Watchdog main loop — runs indefinitely."""
    log.info(
        "Frigate watchdog started  url=%s  container=%s  interval=%ds  threshold=%ds",
        config.frigate_url,
        config.container_name,
        config.check_interval,
        config.stuck_threshold,
    )

    stuck_since: Optional[float] = None

    while True:
        stats = _fetch_stats(config.frigate_url)

        if stats is None:
            # API unreachable counts as stuck
            if stuck_since is None:
                stuck_since = time.monotonic()
                log.warning("Frigate API unreachable — stuck timer started")
        elif _is_capturing(stats):
            if stuck_since is not None:
                log.info("Frigate is capturing again — timer reset")
            stuck_since = None
        else:
            if stuck_since is None:
                stuck_since = time.monotonic()
                log.warning("All cameras report 0 capture_fps — stuck timer started")

        if stuck_since is not None:
            elapsed = time.monotonic() - stuck_since
            log.info("Stuck for %.0fs / %ds threshold", elapsed, config.stuck_threshold)
            if elapsed >= config.stuck_threshold:
                _restart_container(config.container_name)
                stuck_since = None
                # Wait two full intervals before checking again so Frigate has
                # time to come back up before we could trigger another restart.
                time.sleep(config.check_interval * 2)
                continue

        time.sleep(config.check_interval)


if __name__ == "__main__":
    run(Config())
