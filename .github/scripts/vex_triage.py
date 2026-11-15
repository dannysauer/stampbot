#!/usr/bin/env python3
"""Check model triage replies and maintain ``.openvex.json`` for the security scan.

The scheduled security scan asks a language model whether each new Trivy
finding affects Stampbot. This script is the part of that job that must not
guess:

``parse-reply REPLY``
    Read a chat-completions response and print the model's verdict as one JSON
    object with ``affected``, ``justification``, and ``impact_statement``. Exit
    with status 2 when the reply is missing, isn't JSON, or breaks the expected
    format, so the workflow files a manual-review issue instead of failing the
    whole run.

``apply VEX``
    Add a ``not_affected`` statement for one vulnerability, or add packages to
    the existing one, then bump the document ``version`` and ``timestamp``.

``validate VEX``
    Check the document against the OpenVEX 0.2.0 rules this repository relies
    on, and print one line per problem. Exit with status 1 if there are any.

Standard library only, so the workflow can run it with the runner's Python.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

OPENVEX_CONTEXT = "https://openvex.dev/ns/v0.2.0"
STATUSES = {"not_affected", "affected", "fixed", "under_investigation"}
JUSTIFICATIONS = {
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
}
# Models sometimes wrap a JSON answer in a Markdown code fence despite
# response_format; accept exactly one fenced block and nothing else around it.
_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*)\n```\s*$", re.DOTALL | re.IGNORECASE)


class TriageError(ValueError):
    """A model reply or VEX document that can't be used as is."""


def parse_reply(raw: str) -> dict[str, Any]:
    """Return the verdict from a chat-completions response.

    Args:
        raw: The HTTP response body from the chat-completions API.

    Returns:
        ``affected`` (bool), ``justification`` (str, empty when affected), and
        ``impact_statement`` (str).

    Raises:
        TriageError: If the response or the verdict inside it is malformed.
    """
    try:
        response = json.loads(raw)
        content = response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
        raise TriageError(f"the API response has no message content ({error})") from error
    if not isinstance(content, str) or not content.strip():
        raise TriageError("the model returned an empty message")

    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group("body").strip()
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError as error:
        raise TriageError(f"the model's message isn't JSON ({error})") from error
    if not isinstance(verdict, dict):
        raise TriageError("the model's message isn't a JSON object")

    affected = verdict.get("affected")
    if not isinstance(affected, bool):
        raise TriageError("'affected' must be true or false")
    impact = verdict.get("impact_statement")
    if not isinstance(impact, str) or not impact.strip():
        raise TriageError("'impact_statement' must be a non-empty string")
    justification = verdict.get("justification", "")
    if affected:
        justification = ""
    elif justification not in JUSTIFICATIONS:
        raise TriageError(f"'justification' must be one of {sorted(JUSTIFICATIONS)}")
    return {
        "affected": affected,
        "justification": justification,
        "impact_statement": impact.strip(),
    }


def vulnerability_url(vuln_id: str) -> str:
    """Return the advisory URL to use as a vulnerability ``@id``.

    Args:
        vuln_id: A CVE or GHSA identifier.

    Returns:
        The GitHub advisory URL for a GHSA, otherwise the NVD URL.
    """
    if vuln_id.upper().startswith("GHSA-"):
        return f"https://github.com/advisories/{vuln_id}"
    return f"https://nvd.nist.gov/vuln/detail/{vuln_id}"


def apply_not_affected(
    document: dict[str, Any],
    *,
    vuln_id: str,
    title: str,
    justification: str,
    impact_statement: str,
    products: Sequence[str],
    timestamp: str,
) -> dict[str, Any]:
    """Record that ``vuln_id`` doesn't affect the given products.

    Every existing ``not_affected`` statement for the vulnerability keeps its
    package products unchanged, gains the new ones, and takes the new
    justification and impact statement. A container-wide product (``pkg:oci/``)
    is dropped, since the statement now names packages. When there is no such
    statement, a new one is appended.

    Args:
        document: The parsed OpenVEX document; it is modified in place.
        vuln_id: The vulnerability identifier.
        title: A short description of the vulnerability.
        justification: An OpenVEX justification value.
        impact_statement: Why the products aren't affected.
        products: Package URLs of the affected packages.
        timestamp: The new document timestamp, in ISO 8601 UTC.

    Returns:
        The same document, for convenience.

    Raises:
        TriageError: If the justification is unknown or no product is given.
    """
    if justification not in JUSTIFICATIONS:
        raise TriageError(f"unknown justification {justification!r}")
    new_products = [{"@id": purl} for purl in dict.fromkeys(products) if purl]
    if not new_products:
        raise TriageError("at least one product package URL is required")

    statements = document.setdefault("statements", [])
    matched = False
    for statement in statements:
        if (
            statement.get("vulnerability", {}).get("name") != vuln_id
            or statement.get("status") != "not_affected"
        ):
            continue
        matched = True
        merged: dict[str, Any] = {}
        for product in [*statement.get("products", []), *new_products]:
            product_id = str(product.get("@id", ""))
            if product_id.startswith("pkg:oci/"):
                continue
            # The first entry wins, so existing products keep any extra fields.
            merged.setdefault(product_id, product)
        statement["products"] = [merged[key] for key in sorted(merged)]
        statement["justification"] = justification
        statement["impact_statement"] = impact_statement
    if not matched:
        statements.append(
            {
                "vulnerability": {
                    "@id": vulnerability_url(vuln_id),
                    "name": vuln_id,
                    "description": title,
                },
                "products": new_products,
                "status": "not_affected",
                "justification": justification,
                "impact_statement": impact_statement,
            }
        )
    version = document.get("version")
    document["version"] = (version if isinstance(version, int) and version > 0 else 0) + 1
    document["timestamp"] = timestamp
    return document


def validate(document: Any) -> list[str]:
    """Return problems that make ``document`` unusable as Stampbot's VEX.

    This is stricter than OpenVEX 0.2.0 in two ways that match how Stampbot
    writes VEX: every statement needs ``products``, and every product needs an
    ``@id`` package URL rather than only ``identifiers`` or hashes.

    Args:
        document: The parsed OpenVEX document.

    Returns:
        One message per problem; empty when the document is valid.
    """
    if not isinstance(document, Mapping):
        return ["the document isn't a JSON object"]
    problems: list[str] = []
    if document.get("@context") != OPENVEX_CONTEXT:
        problems.append(f"'@context' must be {OPENVEX_CONTEXT!r}")
    for field in ("@id", "author", "timestamp"):
        if not isinstance(document.get(field), str) or not document.get(field):
            problems.append(f"'{field}' must be a non-empty string")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        problems.append("'version' must be a positive integer")
    statements = document.get("statements")
    if not isinstance(statements, list):
        return [*problems, "'statements' must be a list"]

    seen: set[tuple[str, str, str]] = set()
    for index, statement in enumerate(statements):
        where = f"statement {index}"
        if not isinstance(statement, Mapping):
            problems.append(f"{where} isn't a JSON object")
            continue
        vulnerability = statement.get("vulnerability")
        name = vulnerability.get("name") if isinstance(vulnerability, Mapping) else None
        if not isinstance(name, str) or not name:
            problems.append(f"{where}: 'vulnerability.name' must be a non-empty string")
            name = "?"
        where = f"statement {index} ({name})"
        products = statement.get("products")
        product_ids = (
            [p.get("@id") if isinstance(p, Mapping) else None for p in products]
            if isinstance(products, list)
            else []
        )
        if not product_ids or not all(isinstance(p, str) and p for p in product_ids):
            problems.append(f"{where}: every product needs a non-empty '@id'")
        status = statement.get("status")
        if status not in STATUSES:
            problems.append(f"{where}: 'status' must be one of {sorted(STATUSES)}")
        justification = statement.get("justification")
        if justification is not None and justification not in JUSTIFICATIONS:
            problems.append(f"{where}: unknown justification {justification!r}")
        if status == "not_affected" and not (justification or statement.get("impact_statement")):
            problems.append(f"{where}: not_affected needs a justification or impact_statement")
        if status == "affected" and not statement.get("action_statement"):
            problems.append(f"{where}: affected needs an action_statement")
        for product_id in product_ids:
            key = (str(name), str(product_id), str(status))
            if key in seen:
                problems.append(f"{where}: duplicate statement for {product_id}")
            seen.add(key)
    return problems


def _read_json(path: Path) -> Any:
    """Return the parsed JSON in ``path``."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, document: Any) -> None:
    """Write ``document`` to ``path`` in the repository's two-space format."""
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    reply = commands.add_parser("parse-reply", help="print a validated model verdict")
    reply.add_argument("reply", type=Path, help="file holding the API response body")

    apply = commands.add_parser("apply", help="record a not_affected statement")
    apply.add_argument("vex", type=Path)
    apply.add_argument("--id", required=True, dest="vuln_id")
    apply.add_argument("--title", required=True)
    apply.add_argument("--justification", required=True)
    apply.add_argument("--impact", required=True)
    apply.add_argument("--products", required=True, help="JSON list of package URLs")
    apply.add_argument("--timestamp", required=True)

    check = commands.add_parser("validate", help="check an OpenVEX document")
    check.add_argument("vex", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "parse-reply":
            print(json.dumps(parse_reply(args.reply.read_text(encoding="utf-8"))))
            return 0
        if args.command == "apply":
            products = json.loads(args.products)
            if not isinstance(products, list) or not all(isinstance(p, str) for p in products):
                raise TriageError("--products must be a JSON list of strings")
            document = _read_json(args.vex)
            apply_not_affected(
                document,
                vuln_id=args.vuln_id,
                title=args.title,
                justification=args.justification,
                impact_statement=args.impact,
                products=products,
                timestamp=args.timestamp,
            )
            _write_json(args.vex, document)
            return 0
        problems = validate(_read_json(args.vex))
        for problem in problems:
            print(f"{args.vex}: {problem}")
        return 1 if problems else 0
    except (
        TriageError,
        json.JSONDecodeError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        print(f"vex_triage {args.command}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
