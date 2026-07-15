# Security requirements

This page records the properties Stampbot changes must preserve. It is a
review checklist, not a claim that one control makes the whole deployment
secure.

## Authenticate every webhook

- `POST /webhook` must verify `X-Hub-Signature-256` against the raw body before
  parsing the payload.
- Signature comparison must remain constant-time.
- The service must reject missing or invalid signatures.
- Webhook bodies must remain bounded. The current limit is 1 MiB.

## Keep approval authority narrow

- GitHub API calls must use App and installation authentication, not a personal
  access token.
- The App must request only the permissions listed in the
  [configuration reference](configuration.md#github-app-permissions).
- Approve and unapprove ChatOps commands must enforce the configured repository
  permission.
- Stampbot must create and dismiss only its own reviews.
- Stampbot must not merge pull requests or change branch protection.

## Preserve repository policy

- Label-driven approval must pass every configured filter category.
- User and team allowlists are alternatives within the same author category.
- ChatOps approval must remain separate from label eligibility filters.
- Reapproval after a new commit must remain an explicit repository choice.
- Invalid TOML, permission values, and regular expressions must stop automation
  for that event.
- Pull request title matching must bound title length, pattern count, pattern
  length, and per-pattern execution time. Matching must stay off the asyncio
  event loop, and a timeout or engine failure must not create an approval.
- A repository-policy read failure may use service defaults only while that
  behavior is documented and observable.

## Handle secrets as credentials

- Private keys, webhook secrets, installation tokens, cloud credentials, and
  kubeconfigs must not enter source control, logs, examples, or issue reports.
- Local credentials belong in ignored files or environment variables.
- Production credentials belong in a secret manager or a Kubernetes Secret
  with controlled access.
- `/setup` must be opt-in, use an operator-configured trusted public URL, and
  close automatically after App credentials are present. Reopening it on a
  configured instance requires a separate explicit control.
- Setup HTML must not be cached or framed, and the credential callback must not
  send its URL as a referrer.
- Public deployments must protect `/metrics` outside the app when its contents
  shouldn't be public.
- A reverse proxy header may supply `client_ip` only when the operator trusts
  the proxy that writes it.

## Keep the supply chain inspectable

- Python dependencies, workflow actions, and container bases must follow the
  repository's pinning policy.
- CI must keep CodeQL, secret detection, fuzzing, and container scanning active.
- Release assets must state which signatures and attestations actually exist.
  Verification docs must not promise artifacts that weren't published.
- Helm deployments should pin a verified chart version and image digest.

See [Verify a release](release-verification.md) for current commands and known
artifact limits.

## Make failures useful without leaking data

- Logs must name the failed operation without printing tokens or private keys.
- Error messages returned to webhook senders must not expose internal secrets.
- Metric labels must stay bounded; repository names, pull request numbers, and
  user-controlled text don't belong in metric labels.
- Security-relevant failures need enough structured context to correlate them
  with a GitHub delivery.

The [operations runbook](operations.md) lists the evidence maintainers need and
the data they must remove before sharing it.
