# Security Requirements

This document records the security properties Stampbot is expected to preserve.

## Authentication and Authorization

- Webhook requests must be authenticated with GitHub's `X-Hub-Signature-256` HMAC
  signature before event payloads are trusted.
- GitHub API access must use GitHub App authentication and installation tokens, not
  long-lived personal access tokens.
- ChatOps approval and dismissal commands must require the configured repository
  permission threshold.
- Stampbot must only approve or dismiss its own pull request reviews.

## Pull Request Approval Safety

- Adding one configured approval label may approve a pull request only when all configured
  eligibility filters pass.
- Removing approval labels must dismiss Stampbot's approval when the pull request no
  longer satisfies approval policy.
- Duplicate label events must not create duplicate approvals for the same pull request
  head commit.
- ChatOps approval must be able to refresh a stale Stampbot approval after new commits are
  pushed.
- Reapproval after new commits must remain opt-in per repository.

## Secret Handling

- GitHub App private keys, webhook secrets, tokens, and cloud credentials must not be
  committed to the repository.
- Local development secrets belong in ignored files or environment variables.
- CI must continue to run secret detection and GitHub secret scanning/push protection.

## Supply Chain

- GitHub Actions must be pinned according to the repository pinning policy, except where a
  tool explicitly requires semantic tag references for verification compatibility.
- Python dependencies and generated lock/requirements files must remain pinned.
- Container images must be built from pinned base images and scanned before release.
- Releases should include SBOM, VEX, Sigstore signatures, and SLSA provenance artifacts.

## Observability

- Security-relevant failures should be logged with structured context, without leaking
  secrets.
- Metrics should use bounded-cardinality labels.
