# Record a vulnerability that doesn't affect Stampbot

Image scanners report every vulnerable package in the container, including
packages Stampbot never runs. When a finding can't affect Stampbot, record that
in the OpenVEX document instead of suppressing it. CI stops failing on it, and
anyone who pulls a release image can see why it doesn't apply.

Stampbot keeps one OpenVEX v0.2.0 document, `.openvex.json`, at the repository
root.

- Trivy reads it through `.trivy-vex.yaml` in the pull request, push, and
  scheduled scans.
- The release workflow attests it to the image with `cosign attest --type
  openvex` and publishes it as a release asset, such as
  `stampbot-1.11.9.vex.json`. The
  [release verification guide](release-verification.md) shows how to check it.

Record findings in `.openvex.json`, not `.trivyignore`. An ignore entry only
quiets this repository's scanner, while a VEX statement travels with the image
to everyone downstream.

## Before you start

Confirm the finding really doesn't affect Stampbot. A statement says Stampbot
isn't affected, so it needs evidence, not a guess. Check:

- whether Stampbot's code or a runtime dependency in `requirements.txt` loads
  the vulnerable library or calls the vulnerable function;
- whether an attacker can reach that code with input they control.

If you can show neither, fix the finding by updating the dependency or the base
image instead.

If Stampbot is affected and no fix exists yet, a `not_affected` statement would
be false. Add a statement with `"status": "under_investigation"` and an
`action_statement` that says what will resolve it. That status doesn't stop
Trivy from failing, so the repository currently also lists those IDs in
`.trivyignore`, with a comment linking the advisory. Remove both once a fix
ships.

The scheduled security scan asks a GitHub Models language model whether each
new finding affects Stampbot. The model writes the justification and impact
statement from a fixed description of the app, without reading Stampbot's
code. Findings it judges unaffected go into a
`chore(security): update VEX statements from scheduled scan` pull request, and
findings it judges affected become `Security: CVE-… affects stampbot` issues.
Check each statement in that pull request against the steps above before
merging it.

## Add a statement

1. Find the vulnerability ID and the package URL Trivy reported. Drop the
   `@version` and any `?qualifiers` from the package URL, leaving, for example,
   `pkg:deb/debian/libsqlite3-0`.
2. Add a statement to the `statements` array in `.openvex.json`:

   ```json
   {
     "vulnerability": {
       "@id": "https://nvd.nist.gov/vuln/detail/CVE-2026-119373",
       "name": "CVE-2026-119373",
       "description": "sqlite: Denial of service via heap-based buffer overflow in FTS5"
     },
     "products": [{"@id": "pkg:deb/debian/libsqlite3-0"}],
     "status": "not_affected",
     "justification": "vulnerable_code_not_in_execute_path",
     "impact_statement": "Why Stampbot can't reach the vulnerable code, with evidence."
   }
   ```

   If a statement for the same vulnerability already exists, add the package to
   its `products` list instead.
3. Choose the `justification` that matches your evidence:

   | Justification | Use when |
   | --- | --- |
   | `component_not_present` | The package isn't in the image at all. |
   | `vulnerable_code_not_present` | The package is present, but not the vulnerable part, such as a daemon built from the same source package. |
   | `vulnerable_code_not_in_execute_path` | The vulnerable code is present, but Stampbot never loads or calls it. |
   | `vulnerable_code_cannot_be_controlled_by_adversary` | Stampbot runs the code, but only with input an attacker can't influence. |
   | `inline_mitigations_already_exist` | Something else in the image or deployment blocks the attack. |

4. Write the `impact_statement` for a stranger auditing the image. Say what
   the package is doing in the image, what the attack needs, and why Stampbot
   doesn't provide it.
5. Increase the top-level `version` by one and set `timestamp` to the current
   UTC time, such as `2026-11-03T10:48:18Z`.

## Check the result

Confirm the file is still valid JSON and that your statement is present:

```bash
jq -e '.statements[] | select(.vulnerability.name == "CVE-2026-119373")' .openvex.json
```

The command prints the statement and exits with status 0. A non-zero status
means the file didn't parse or the statement is missing.

Then push the change. The `Security Scan (PR)` check reruns Trivy with the
updated document. It passes once every CRITICAL or HIGH finding is fixed,
covered by a `not_affected` statement, or listed in `.trivyignore`.
