# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Atheris fuzz target for GitHub webhook signature verification."""

from __future__ import annotations

import sys

import atheris

with atheris.instrument_imports(enable_loader_override=False):
    from stampbot.webhook_handler import WebhookHandler

MAX_PAYLOAD_SIZE = 8192
MAX_SIGNATURE_SIZE = 256

handler = WebhookHandler()
handler._webhook_secret = b"clusterfuzzlite-test-secret"


def TestOneInput(data: bytes) -> None:
    """Verify arbitrary payload and signature byte combinations."""
    signature_bytes = data[:MAX_SIGNATURE_SIZE]
    payload = data[MAX_SIGNATURE_SIZE : MAX_SIGNATURE_SIZE + MAX_PAYLOAD_SIZE]
    signature = signature_bytes.decode("utf-8", errors="ignore")

    handler.verify_signature(payload, signature)


def main() -> None:
    """Run the fuzz target."""
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
