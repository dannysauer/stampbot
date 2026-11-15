"""Tests for the security scan's VEX triage helper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / ".github" / "scripts" / "vex_triage.py"


@pytest.fixture(scope="module")
def triage() -> ModuleType:
    """Load the script as a module; ``scripts/`` is deliberately not a package."""
    spec = importlib.util.spec_from_file_location("vex_triage", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reply(content: Any) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]})


def _verdict(**fields: Any) -> str:
    base = {
        "affected": False,
        "justification": "vulnerable_code_not_in_execute_path",
        "impact_statement": "Stampbot never loads the library.",
    }
    base.update(fields)
    return json.dumps(base)


def _document() -> dict[str, Any]:
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": "https://example.com/vex.json",
        "author": "Example",
        "timestamp": "2026-01-01T00:00:00Z",
        "version": 3,
        "statements": [],
    }


def test_repository_vex_document_is_valid(triage: ModuleType) -> None:
    """The committed .openvex.json passes the same check the scan runs."""
    document = json.loads((_ROOT / ".openvex.json").read_text(encoding="utf-8"))
    assert triage.validate(document) == []


def test_parse_reply_accepts_plain_json(triage: ModuleType) -> None:
    """A well-formed reply yields the verdict."""
    assert triage.parse_reply(_reply(_verdict())) == {
        "affected": False,
        "justification": "vulnerable_code_not_in_execute_path",
        "impact_statement": "Stampbot never loads the library.",
    }


def test_parse_reply_accepts_one_code_fence(triage: ModuleType) -> None:
    """A fenced answer, the cause of the November 1 crash, is unwrapped."""
    fenced = f"```json\n{_verdict()}\n```"
    assert triage.parse_reply(_reply(fenced))["affected"] is False


@pytest.mark.parametrize("fence", ["```", "```JSON"])
def test_parse_reply_accepts_other_fence_tags(triage: ModuleType, fence: str) -> None:
    """An untagged or upper-case fence is unwrapped too."""
    assert triage.parse_reply(_reply(f"{fence}\n{_verdict()}\n```"))["affected"] is False


def test_parse_reply_drops_justification_when_affected(triage: ModuleType) -> None:
    """An affected verdict needs no justification and keeps none."""
    verdict = triage.parse_reply(_reply(_verdict(affected=True, justification="nonsense")))
    assert verdict == {
        "affected": True,
        "justification": "",
        "impact_statement": "Stampbot never loads the library.",
    }


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not json", "no message content"),
        (json.dumps({"choices": []}), "no message content"),
        (_reply(None), "empty message"),
        (_reply("   "), "empty message"),
        (_reply("Sure! Here is the JSON: {}"), "isn't JSON"),
        (_reply("```json\n{}\n```\nHope that helps"), "isn't JSON"),
        (_reply("[1, 2]"), "isn't a JSON object"),
        (_reply(_verdict(affected="false")), "'affected'"),
        (_reply(_verdict(impact_statement="")), "'impact_statement'"),
        (_reply(_verdict(justification="trust_me")), "'justification'"),
    ],
)
def test_parse_reply_rejects_unusable_replies(triage: ModuleType, raw: str, message: str) -> None:
    """Every unusable reply raises a typed error instead of crashing the scan."""
    with pytest.raises(triage.TriageError, match=message):
        triage.parse_reply(raw)


def test_apply_appends_new_statement(triage: ModuleType) -> None:
    """A new vulnerability gets a statement and the document version advances."""
    document = triage.apply_not_affected(
        _document(),
        vuln_id="GHSA-aaaa-bbbb-cccc",
        title="Example flaw",
        justification="component_not_present",
        impact_statement="Not installed.",
        products=["pkg:pypi/example", "pkg:pypi/example"],
        timestamp="2026-02-02T00:00:00Z",
    )
    assert document["version"] == 4
    assert document["timestamp"] == "2026-02-02T00:00:00Z"
    assert document["statements"] == [
        {
            "vulnerability": {
                "@id": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                "name": "GHSA-aaaa-bbbb-cccc",
                "description": "Example flaw",
            },
            "products": [{"@id": "pkg:pypi/example"}],
            "status": "not_affected",
            "justification": "component_not_present",
            "impact_statement": "Not installed.",
        }
    ]
    assert triage.validate(document) == []


def test_apply_extends_existing_statement(triage: ModuleType) -> None:
    """An existing not_affected statement gains packages and drops a container product."""
    document = _document()
    document["statements"].append(
        {
            "vulnerability": {"name": "CVE-2026-0001"},
            "products": [{"@id": "pkg:oci/stampbot"}, {"@id": "pkg:deb/debian/b"}],
            "status": "not_affected",
            "justification": "component_not_present",
        }
    )
    triage.apply_not_affected(
        document,
        vuln_id="CVE-2026-0001",
        title="ignored for existing statements",
        justification="vulnerable_code_not_present",
        impact_statement="Only the client library ships.",
        products=["pkg:deb/debian/a"],
        timestamp="2026-02-02T00:00:00Z",
    )
    statement = document["statements"][0]
    assert statement["products"] == [{"@id": "pkg:deb/debian/a"}, {"@id": "pkg:deb/debian/b"}]
    assert statement["justification"] == "vulnerable_code_not_present"
    assert statement["impact_statement"] == "Only the client library ships."
    assert len(document["statements"]) == 1


def test_vulnerability_url_uses_nvd_for_cves(triage: ModuleType) -> None:
    """A CVE identifier links to NVD."""
    assert triage.vulnerability_url("CVE-2026-0001").startswith("https://nvd.nist.gov/")


@pytest.mark.parametrize(
    ("justification", "products", "message"),
    [
        ("made_up", ["pkg:pypi/x"], "unknown justification"),
        ("component_not_present", [""], "at least one product"),
    ],
)
def test_apply_rejects_bad_input(
    triage: ModuleType, justification: str, products: list[str], message: str
) -> None:
    """apply refuses to write an invalid statement."""
    with pytest.raises(triage.TriageError, match=message):
        triage.apply_not_affected(
            _document(),
            vuln_id="CVE-2026-0001",
            title="t",
            justification=justification,
            impact_statement="i",
            products=products,
            timestamp="2026-02-02T00:00:00Z",
        )


def test_validate_reports_each_problem(triage: ModuleType) -> None:
    """validate names every broken rule."""
    document = {
        "@context": "wrong",
        "author": "",
        "timestamp": "t",
        "version": True,
        "statements": [
            "not an object",
            {"vulnerability": {}, "products": [{}], "status": "maybe"},
            {
                "vulnerability": {"name": "CVE-1"},
                "products": [{"@id": "pkg:x"}],
                "status": "not_affected",
                "justification": "trust_me",
            },
            {
                "vulnerability": {"name": "CVE-2"},
                "products": [{"@id": "pkg:x"}],
                "status": "not_affected",
            },
            {
                "vulnerability": {"name": "CVE-3"},
                "products": [{"@id": "pkg:x"}],
                "status": "affected",
            },
            {
                "vulnerability": {"name": "CVE-3"},
                "products": [{"@id": "pkg:x"}],
                "status": "affected",
                "action_statement": "Upgrade.",
            },
        ],
    }
    problems = "\n".join(triage.validate(document))
    for expected in [
        "'@context'",
        "'@id' must be",
        "'author' must be",
        "'version' must be",
        "statement 0 isn't a JSON object",
        "'vulnerability.name'",
        "non-empty '@id'",
        "'status' must be one of",
        "unknown justification 'trust_me'",
        "(CVE-2): not_affected needs",
        "(CVE-3): affected needs an action_statement",
        "duplicate statement for pkg:x",
    ]:
        assert expected in problems


def test_validate_rejects_non_documents(triage: ModuleType) -> None:
    """A non-object or a missing statements list is reported."""
    assert triage.validate([]) == ["the document isn't a JSON object"]
    assert triage.validate({**_document(), "statements": None})[-1] == "'statements' must be a list"


def test_cli_round_trip(
    triage: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The subcommands work end to end on files."""
    reply = tmp_path / "reply.json"
    reply.write_text(_reply(_verdict()), encoding="utf-8")
    assert triage.main(["parse-reply", str(reply)]) == 0
    assert json.loads(capsys.readouterr().out)["affected"] is False

    vex = tmp_path / "vex.json"
    vex.write_text(json.dumps(_document()), encoding="utf-8")
    assert (
        triage.main(
            [
                "apply",
                str(vex),
                "--id",
                "CVE-2026-0002",
                "--title",
                "t",
                "--justification",
                "component_not_present",
                "--impact",
                "i",
                "--products",
                '["pkg:pypi/x"]',
                "--timestamp",
                "2026-02-02T00:00:00Z",
            ]
        )
        == 0
    )
    assert triage.main(["validate", str(vex)]) == 0
    assert json.loads(vex.read_text(encoding="utf-8"))["version"] == 4


def test_cli_reports_errors(
    triage: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bad input exits 2 with a message, and invalid documents exit 1."""
    reply = tmp_path / "reply.json"
    reply.write_text("nope", encoding="utf-8")
    assert triage.main(["parse-reply", str(reply)]) == 2
    assert "no message content" in capsys.readouterr().err

    vex = tmp_path / "vex.json"
    vex.write_text(json.dumps(_document()), encoding="utf-8")
    base = [
        "apply",
        str(vex),
        "--id",
        "C",
        "--title",
        "t",
        "--justification",
        "component_not_present",
    ]
    tail = ["--impact", "i", "--timestamp", "x"]
    assert triage.main([*base, "--products", '"not a list"', *tail]) == 2
    assert "--products must be" in capsys.readouterr().err

    vex.write_text(json.dumps({**_document(), "version": 0}), encoding="utf-8")
    assert triage.main(["validate", str(vex)]) == 1
    assert "'version' must be" in capsys.readouterr().out


def test_apply_keeps_existing_product_objects(triage: ModuleType) -> None:
    """Merging never replaces an existing product, so its extra fields survive."""
    document = _document()
    existing = {"@id": "pkg:deb/debian/a", "subcomponents": [{"@id": "pkg:deb/debian/a-sub"}]}
    document["statements"].append(
        {
            "vulnerability": {"name": "CVE-2026-0003"},
            "products": [existing],
            "status": "not_affected",
            "justification": "component_not_present",
        }
    )
    triage.apply_not_affected(
        document,
        vuln_id="CVE-2026-0003",
        title="t",
        justification="component_not_present",
        impact_statement="i",
        products=["pkg:deb/debian/a"],
        timestamp="2026-02-02T00:00:00Z",
    )
    assert document["statements"][0]["products"] == [existing]


def test_apply_updates_every_matching_statement(triage: ModuleType) -> None:
    """Each not_affected statement for the vulnerability gets the update."""
    document = _document()
    for package in ("a", "b"):
        document["statements"].append(
            {
                "vulnerability": {"name": "CVE-2026-0004"},
                "products": [{"@id": f"pkg:deb/debian/{package}"}],
                "status": "not_affected",
                "justification": "component_not_present",
            }
        )
    triage.apply_not_affected(
        document,
        vuln_id="CVE-2026-0004",
        title="t",
        justification="vulnerable_code_not_present",
        impact_statement="i",
        products=["pkg:deb/debian/c"],
        timestamp="2026-02-02T00:00:00Z",
    )
    assert [s["justification"] for s in document["statements"]] == [
        "vulnerable_code_not_present",
        "vulnerable_code_not_present",
    ]


@pytest.mark.parametrize("version", [None, "7", 0])
def test_apply_starts_version_at_one_when_missing_or_invalid(
    triage: ModuleType, version: Any
) -> None:
    """A missing or unusable version becomes 1 instead of crashing."""
    document = {**_document(), "version": version}
    triage.apply_not_affected(
        document,
        vuln_id="CVE-2026-0005",
        title="t",
        justification="component_not_present",
        impact_statement="i",
        products=["pkg:pypi/x"],
        timestamp="2026-02-02T00:00:00Z",
    )
    assert document["version"] == 1


def test_validate_flags_non_object_products(triage: ModuleType) -> None:
    """A product that isn't an object is reported, not skipped."""
    document = _document()
    document["statements"].append(
        {
            "vulnerability": {"name": "CVE-2026-0006"},
            "products": [{"@id": "pkg:pypi/x"}, "pkg:pypi/y"],
            "status": "not_affected",
            "justification": "component_not_present",
        }
    )
    assert any("non-empty '@id'" in problem for problem in triage.validate(document))


def test_cli_treats_dash_values_as_data(triage: ModuleType, tmp_path: Path) -> None:
    """--flag=value keeps a model-supplied "--help" from being parsed as an option."""
    vex = tmp_path / "vex.json"
    vex.write_text(json.dumps(_document()), encoding="utf-8")
    assert (
        triage.main(
            [
                "apply",
                str(vex),
                "--id=CVE-2026-0007",
                "--title=--title",
                "--justification=component_not_present",
                "--impact=--help",
                '--products=["pkg:pypi/x"]',
                "--timestamp=2026-02-02T00:00:00Z",
            ]
        )
        == 0
    )
    statement = json.loads(vex.read_text(encoding="utf-8"))["statements"][0]
    assert statement["impact_statement"] == "--help"
