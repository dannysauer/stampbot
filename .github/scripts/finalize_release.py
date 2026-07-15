#!/usr/bin/env python3
"""Attach provenance to one exact draft release and publish it fail-closed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
UPLOAD_ROOT = "https://uploads.github.com"
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_PROVENANCE_BYTES = 10 * 1024 * 1024


class GitHubReleaseClient:
    """Minimal GitHub REST client for exact-ID release finalization."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GH_TOKEN is required")
        self._token = token

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        content_type: str = "application/vnd.github+json",
    ) -> Any:
        """Send an authenticated request and decode its JSON response."""
        if not (url.startswith(f"{API_ROOT}/") or url.startswith(f"{UPLOAD_ROOT}/")):
            raise ValueError("GitHub API URL is outside the allowed HTTPS origins")
        request = urllib.request.Request(  # noqa: S310 - URL origins are allowlisted above.
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": content_type,
                "User-Agent": "stampbot-release-finalizer",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                if response.status == 204:
                    return None
                return json.load(response)
        except urllib.error.HTTPError as error:
            body = error.read(1024).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub API request failed with HTTP {error.code}: {body}"
            ) from error

    def get_release(self, repository: str, release_id: int) -> dict[str, Any]:
        """Fetch one release by numeric ID."""
        url = f"{API_ROOT}/repos/{repository}/releases/{release_id}"
        release = self.request_json("GET", url)
        if not isinstance(release, dict):
            raise RuntimeError("GitHub returned a non-object release response")
        return release

    def list_assets(self, repository: str, release_id: int) -> list[dict[str, Any]]:
        """Fetch every asset for one numeric release ID."""
        assets: list[dict[str, Any]] = []
        for page in range(1, 1001):
            url = (
                f"{API_ROOT}/repos/{repository}/releases/{release_id}/assets"
                f"?per_page=100&page={page}"
            )
            result = self.request_json("GET", url)
            if not isinstance(result, list) or not all(isinstance(asset, dict) for asset in result):
                raise RuntimeError("GitHub returned an invalid release asset response")
            assets.extend(result)
            if len(result) < 100:
                return assets
        raise RuntimeError("release asset pagination exceeded the safety limit")

    def upload_asset(self, upload_url: str, path: Path) -> dict[str, Any]:
        """Upload one file through the captured release upload URL."""
        if path.stat().st_size > MAX_PROVENANCE_BYTES:
            raise ValueError("provenance artifact exceeds the 10 MiB safety limit")
        base_url = upload_url.removesuffix("{?name,label}")
        url = f"{base_url}?{urllib.parse.urlencode({'name': path.name})}"
        asset = self.request_json(
            "POST", url, data=path.read_bytes(), content_type="application/octet-stream"
        )
        if not isinstance(asset, dict):
            raise RuntimeError("GitHub returned a non-object asset response")
        return asset

    def delete_asset(self, repository: str, asset_id: int) -> None:
        """Delete one release asset by its exact numeric ID."""
        if not REPOSITORY.fullmatch(repository):
            raise ValueError("repository must use the owner/name form")
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ValueError("asset ID must be a positive integer")
        url = f"{API_ROOT}/repos/{repository}/releases/assets/{asset_id}"
        if self.request_json("DELETE", url) is not None:
            raise RuntimeError("GitHub returned an unexpected release asset deletion response")

    def publish_release(self, repository: str, release_id: int) -> dict[str, Any]:
        """Publish one release by numeric ID."""
        url = f"{API_ROOT}/repos/{repository}/releases/{release_id}"
        release = self.request_json("PATCH", url, data=json.dumps({"draft": False}).encode("utf-8"))
        if not isinstance(release, dict):
            raise RuntimeError("GitHub returned a non-object release response")
        return release


def validate_upload_url(repository: str, release_id: int, upload_url: str) -> None:
    """Require the canonical upload URL for the exact repository and release ID."""
    expected = f"{UPLOAD_ROOT}/repos/{repository}/releases/{release_id}/assets{{?name,label}}"
    if upload_url != expected:
        raise ValueError("captured release upload URL does not match the exact release ID")


def validate_release(
    release: dict[str, Any],
    *,
    release_id: int,
    tag: str,
    upload_url: str,
    draft: bool,
) -> None:
    """Validate the identity and state of one numeric release response."""
    if release.get("id") != release_id:
        raise RuntimeError("GitHub returned a different release ID")
    if release.get("tag_name") != tag:
        raise RuntimeError("the exact release ID has an unexpected tag")
    if release.get("upload_url") != upload_url:
        raise RuntimeError("the exact release ID has an unexpected upload URL")
    if release.get("draft") is not draft:
        state = "draft" if draft else "published"
        raise RuntimeError(f"the exact release ID is not {state}")


def validate_asset_names(assets: list[dict[str, Any]], expected_names: list[str]) -> None:
    """Require one copy of every expected asset and no other assets."""
    if any(not name or Path(name).name != name for name in expected_names):
        raise ValueError("expected asset names must be non-empty basenames")
    if len(set(expected_names)) != len(expected_names):
        raise ValueError("expected asset names must be unique")

    actual_names: list[str] = []
    for asset in assets:
        name = asset.get("name")
        if not isinstance(name, str):
            raise RuntimeError("release contains an asset without a valid name")
        actual_names.append(name)
    if any(asset.get("state") != "uploaded" for asset in assets):
        raise RuntimeError("release contains an asset that has not finished uploading")
    if any(
        isinstance(asset.get("size"), bool)
        or not isinstance(asset.get("size"), int)
        or asset["size"] <= 0
        for asset in assets
    ):
        raise RuntimeError("release contains an empty asset or an asset without a valid size")
    if Counter(actual_names) != Counter(expected_names):
        raise RuntimeError(
            "release asset set does not match the expected exact set: "
            f"expected={sorted(expected_names)}, actual={sorted(actual_names)}"
        )


def finalize_release(
    client: GitHubReleaseClient,
    *,
    repository: str,
    release_id: int,
    upload_url: str,
    tag: str,
    provenance: Path,
    expected_assets: list[str],
) -> None:
    """Validate, complete, and publish one exact draft release."""
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("repository must use the owner/name form")
    if release_id <= 0:
        raise ValueError("release ID must be a positive integer")
    if not tag or any(character.isspace() for character in tag):
        raise ValueError("release tag must be non-empty and contain no whitespace")
    if not provenance.is_file() or provenance.stat().st_size == 0:
        raise ValueError("provenance artifact is missing or empty")
    if provenance.stat().st_size > MAX_PROVENANCE_BYTES:
        raise ValueError("provenance artifact exceeds the 10 MiB safety limit")
    if provenance.name not in expected_assets:
        raise ValueError("provenance filename is absent from the expected asset set")

    validate_upload_url(repository, release_id, upload_url)
    draft_release = client.get_release(repository, release_id)
    validate_release(
        draft_release,
        release_id=release_id,
        tag=tag,
        upload_url=upload_url,
        draft=True,
    )

    base_assets = [name for name in expected_assets if name != provenance.name]
    draft_assets = client.list_assets(repository, release_id)
    try:
        validate_asset_names(draft_assets, base_assets)
        draft_has_provenance = False
    except RuntimeError:
        try:
            validate_asset_names(draft_assets, expected_assets)
        except RuntimeError as error:
            raise RuntimeError(
                "draft release asset set does not match the expected exact set "
                "(base-only or complete)"
            ) from error
        draft_has_provenance = True

    upload_provenance = True
    if draft_has_provenance:
        provenance_assets = [
            asset for asset in draft_assets if asset.get("name") == provenance.name
        ]
        if len(provenance_assets) != 1:
            raise RuntimeError("release must contain exactly one provenance asset")
        provenance_asset = provenance_assets[0]
        asset_id = provenance_asset.get("id")
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise RuntimeError("provenance asset does not have a valid numeric ID")

        local_digest = hashlib.sha256(provenance.read_bytes()).hexdigest()
        remote_digest = provenance_asset.get("digest")
        digest_match = (
            re.fullmatch(r"sha256:([0-9A-Fa-f]{64})", remote_digest)
            if isinstance(remote_digest, str)
            else None
        )
        if digest_match is not None and digest_match.group(1).lower() == local_digest:
            upload_provenance = False
        else:
            client.delete_asset(repository, asset_id)
            validate_asset_names(client.list_assets(repository, release_id), base_assets)
            upload_provenance = True

    if upload_provenance:
        uploaded = client.upload_asset(upload_url, provenance)
        if uploaded.get("name") != provenance.name:
            raise RuntimeError("GitHub reported an unexpected uploaded provenance filename")
        if uploaded.get("state") != "uploaded" or uploaded.get("size") != provenance.stat().st_size:
            raise RuntimeError("GitHub did not confirm the complete provenance upload")

    validate_asset_names(client.list_assets(repository, release_id), expected_assets)
    published = client.publish_release(repository, release_id)
    validate_release(
        published,
        release_id=release_id,
        tag=tag,
        upload_url=upload_url,
        draft=False,
    )
    validate_asset_names(client.list_assets(repository, release_id), expected_assets)


def main() -> None:
    """Parse workflow inputs and finalize the requested release."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-id", type=int, required=True)
    parser.add_argument("--upload-url", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--expected-asset", action="append", required=True)
    args = parser.parse_args()

    client = GitHubReleaseClient(os.environ.get("GH_TOKEN", ""))
    finalize_release(
        client,
        repository=args.repository,
        release_id=args.release_id,
        upload_url=args.upload_url,
        tag=args.tag,
        provenance=args.provenance,
        expected_assets=args.expected_asset,
    )
    print(f"Published exact release ID {args.release_id} with the verified asset set")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"release finalization failed closed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
