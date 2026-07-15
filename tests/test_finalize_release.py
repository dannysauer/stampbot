from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


finalizer = load_script("finalize_release")


class FakeClient:
    def __init__(self, assets: list[str]) -> None:
        self.upload_url = (
            "https://uploads.github.com/repos/acme/stampbot/releases/123/assets{?name,label}"
        )
        self.release = {
            "id": 123,
            "tag_name": "v1.2.3",
            "upload_url": self.upload_url,
            "draft": True,
        }
        self.assets = [
            {"id": 1000 + index, "name": name, "state": "uploaded", "size": 100}
            for index, name in enumerate(assets)
        ]
        self.deleted_asset_ids: list[int] = []
        self.upload_count = 0
        self.delete_error: RuntimeError | None = None
        self.published = False

    def get_release(self, repository: str, release_id: int) -> dict[str, Any]:
        assert repository == "acme/stampbot"
        assert release_id == 123
        return self.release.copy()

    def list_assets(self, repository: str, release_id: int) -> list[dict[str, Any]]:
        assert repository == "acme/stampbot"
        assert release_id == 123
        return [asset.copy() for asset in self.assets]

    def upload_asset(self, upload_url: str, path: Path) -> dict[str, Any]:
        assert upload_url == self.upload_url
        self.upload_count += 1
        asset = {
            "id": 2000 + self.upload_count,
            "name": path.name,
            "state": "uploaded",
            "size": path.stat().st_size,
            "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
        }
        self.assets.append(asset)
        return asset.copy()

    def delete_asset(self, repository: str, asset_id: int) -> None:
        assert repository == "acme/stampbot"
        if self.delete_error is not None:
            raise self.delete_error
        matching_assets = [asset for asset in self.assets if asset.get("id") == asset_id]
        assert len(matching_assets) == 1
        self.assets.remove(matching_assets[0])
        self.deleted_asset_ids.append(asset_id)

    def publish_release(self, repository: str, release_id: int) -> dict[str, Any]:
        assert repository == "acme/stampbot"
        assert release_id == 123
        self.published = True
        self.release["draft"] = False
        return self.release.copy()


def test_finalize_release_publishes_only_after_exact_asset_verification(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json", "vex.json"])

    finalizer.finalize_release(
        client,
        repository="acme/stampbot",
        release_id=123,
        upload_url=client.upload_url,
        tag="v1.2.3",
        provenance=provenance,
        expected_assets=["sbom.json", "vex.json", provenance.name],
    )

    assert client.published is True
    assert client.release["draft"] is False
    assert [asset["name"] for asset in client.assets] == [
        "sbom.json",
        "vex.json",
        provenance.name,
    ]
    assert client.upload_count == 1
    assert client.deleted_asset_ids == []


def test_finalize_release_accepts_an_empty_base_asset_set(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient([])

    finalizer.finalize_release(
        client,
        repository="acme/stampbot",
        release_id=123,
        upload_url=client.upload_url,
        tag="v1.2.3",
        provenance=provenance,
        expected_assets=[provenance.name],
    )

    assert client.published is True
    assert client.upload_count == 1
    assert [asset["name"] for asset in client.assets] == [provenance.name]


def test_finalize_release_reuses_matching_provenance_without_upload(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json", provenance.name])
    client.assets[-1]["digest"] = f"sha256:{hashlib.sha256(provenance.read_bytes()).hexdigest()}"

    finalizer.finalize_release(
        client,
        repository="acme/stampbot",
        release_id=123,
        upload_url=client.upload_url,
        tag="v1.2.3",
        provenance=provenance,
        expected_assets=["sbom.json", provenance.name],
    )

    assert client.published is True
    assert client.upload_count == 0
    assert client.deleted_asset_ids == []


@pytest.mark.parametrize("remote_digest", [f"sha256:{'0' * 64}", None])
def test_finalize_release_replaces_stale_provenance(
    tmp_path: Path, remote_digest: str | None
) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json", provenance.name])
    stale_asset_id = client.assets[-1]["id"]
    if remote_digest is not None:
        client.assets[-1]["digest"] = remote_digest

    finalizer.finalize_release(
        client,
        repository="acme/stampbot",
        release_id=123,
        upload_url=client.upload_url,
        tag="v1.2.3",
        provenance=provenance,
        expected_assets=["sbom.json", provenance.name],
    )

    assert client.published is True
    assert client.deleted_asset_ids == [stale_asset_id]
    assert client.upload_count == 1
    assert [asset["name"] for asset in client.assets] == ["sbom.json", provenance.name]


def test_finalize_release_does_not_publish_when_stale_asset_delete_fails(
    tmp_path: Path,
) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json", provenance.name])
    client.assets[-1]["digest"] = f"sha256:{'0' * 64}"
    client.delete_error = RuntimeError("asset deletion failed")

    with pytest.raises(RuntimeError, match="asset deletion failed"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False
    assert client.upload_count == 0
    assert client.deleted_asset_ids == []


@pytest.mark.parametrize("invalid_asset_set", ["unexpected", "duplicate"])
def test_finalize_release_rejects_unexpected_or_duplicate_retry_assets(
    tmp_path: Path, invalid_asset_set: str
) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    names = ["sbom.json", provenance.name]
    names.append("unexpected.txt" if invalid_asset_set == "unexpected" else provenance.name)
    client = FakeClient(names)

    with pytest.raises(RuntimeError, match="does not match the expected exact set"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False
    assert client.upload_count == 0
    assert client.deleted_asset_ids == []


def test_finalize_release_rejects_provenance_without_numeric_asset_id(
    tmp_path: Path,
) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json", provenance.name])
    client.assets[-1]["id"] = "not-an-id"

    with pytest.raises(RuntimeError, match="valid numeric ID"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False
    assert client.upload_count == 0
    assert client.deleted_asset_ids == []


def test_delete_asset_accepts_exact_api_204(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoContentResponse:
        status = 204

        def __enter__(self) -> NoContentResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: int) -> NoContentResponse:
        assert request.get_method() == "DELETE"
        assert request.full_url == (
            "https://api.github.com/repos/acme/stampbot/releases/assets/9876"
        )
        assert timeout == 60
        return NoContentResponse()

    monkeypatch.setattr(finalizer.urllib.request, "urlopen", fake_urlopen)

    client = finalizer.GitHubReleaseClient("test-token")
    client.delete_asset("acme/stampbot", 9876)


def test_finalize_release_rejects_wrong_numeric_upload_url(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json"])

    with pytest.raises(ValueError, match="does not match the exact release ID"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=(
                "https://uploads.github.com/repos/acme/stampbot/releases/456/assets{?name,label}"
            ),
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False


def test_finalize_release_rejects_incomplete_base_assets(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json"])

    with pytest.raises(RuntimeError, match="does not match the expected exact set"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", "vex.json", provenance.name],
        )

    assert client.published is False
    assert provenance.name not in [asset["name"] for asset in client.assets]


def test_finalize_release_rejects_extra_base_asset(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json", "unexpected.txt"])

    with pytest.raises(RuntimeError, match="does not match the expected exact set"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False


def test_finalize_release_rejects_already_published_id(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json"])
    client.release["draft"] = False

    with pytest.raises(RuntimeError, match="is not draft"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False


def test_finalize_release_rejects_upload_url_mismatch_on_exact_id(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_text("signed provenance", encoding="utf-8")
    client = FakeClient(["sbom.json"])
    captured_upload_url = client.upload_url
    client.release["upload_url"] = (
        "https://uploads.github.com/repos/acme/stampbot/releases/456/assets{?name,label}"
    )

    with pytest.raises(RuntimeError, match="has an unexpected upload URL"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=captured_upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False


def test_finalize_release_rejects_oversized_provenance(tmp_path: Path) -> None:
    provenance = tmp_path / "stampbot-1.2.3.intoto.jsonl"
    provenance.write_bytes(b"x" * (finalizer.MAX_PROVENANCE_BYTES + 1))
    client = FakeClient(["sbom.json"])

    with pytest.raises(ValueError, match="exceeds the 10 MiB"):
        finalizer.finalize_release(
            client,
            repository="acme/stampbot",
            release_id=123,
            upload_url=client.upload_url,
            tag="v1.2.3",
            provenance=provenance,
            expected_assets=["sbom.json", provenance.name],
        )

    assert client.published is False


def test_asset_verification_rejects_duplicates() -> None:
    with pytest.raises(RuntimeError, match="does not match the expected exact set"):
        finalizer.validate_asset_names(
            [
                {"name": "artifact.tgz", "state": "uploaded", "size": 1},
                {"name": "artifact.tgz", "state": "uploaded", "size": 1},
            ],
            ["artifact.tgz"],
        )
