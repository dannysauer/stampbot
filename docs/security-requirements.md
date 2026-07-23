# Security requirements

Use these requirements when changing Stampbot. They describe properties the
implementation and deployment must preserve. They do not replace a threat
model or a review of the environment where Stampbot runs.

## Authenticate before parsing

- Verify `X-Hub-Signature-256` against the raw webhook body.
- Compare signatures in constant time.
- Reject a missing or invalid signature before parsing JSON.
- Keep the declared and actual body limit at 1 MiB or lower.
- Treat every payload field as untrusted after signature verification.

A matching signature proves that the sender knew the webhook secret and that
the body has not changed since signing. When only GitHub and Stampbot's
operators and runtime hold that secret, Stampbot treats the request as a GitHub
delivery. Pull request titles, comments, labels, and repository policy remain
untrusted input.

## Limit approval authority

- Use GitHub App and installation credentials, never a personal access token.
- Request only the permissions in the
  [configuration reference](configuration.md#github-app-permissions).
- Check repository permission before an approve or unapprove ChatOps command.
- Create and dismiss reviews only under Stampbot's App identity.
- Never merge pull requests or change branch protection.
- Keep `reapprove` an explicit repository choice.

GitHub branch rules remain the final enforcement boundary.

## Fail closed on policy input

- Require every configured label-driven filter category to pass.
- Treat user and team allowlists as alternatives within the author category.
- Keep ChatOps authorization separate from label-driven filters.
- Stop automation for readable but invalid TOML.
- Stop automation when GitHub cannot complete a policy read.
- Reject unknown permission names and invalid command lists.
- Bound title length, pattern count, pattern length, and match time.
- Run title matching outside the asyncio event loop.
- Reject the event when matching times out or the regex engine fails.

Service defaults may apply only after every applicable GitHub policy lookup
returns a clean “not found” result.

## Protect credentials and setup

- Keep private keys, webhook secrets, installation tokens, cloud credentials,
  and kubeconfigs out of source control, logs, examples, and issue reports.
- Use ignored local files for development and a managed secret store in
  production.
- Accept a private-key path only when it selects a regular file no larger than
  64 KiB with a complete private-key PEM envelope.
- Leave setup disabled by default.
- Build setup URLs only from the operator-configured trusted base URL.
- Close setup after all App credentials exist.
- Require a separate explicit flag to reopen setup on a configured instance.
- Prevent setup HTML from being cached, framed, or forwarded as a referrer.
- Never return the App ID from the setup status response.

The setup callback displays credentials once. Restrict access to every setup
route during that short provisioning window.

## Isolate operational interfaces

- Never register `/metrics` on the public HTTP listener.
- Keep the separate metrics listener disabled by default.
- Bind source deployments to loopback unless a private monitoring network is
  ready.
- Keep the Helm metrics Service separate from the main Service and Ingress.
- Treat metrics as sensitive because installation IDs and traffic patterns may
  appear in series.
- Use matched route templates or fixed fallback values for HTTP metric labels.
- Never put raw paths, repository names, pull request numbers, or comment text
  in labels.

The metrics listener has no application authentication. Network placement is
its access control.

## Secure telemetry transport

- Use TLS for OTLP by default.
- Permit plaintext only after an explicit literal boolean opt-in.
- Never let a plaintext flag downgrade an HTTPS endpoint.
- Mount a private CA from a Secret or equivalent secret store.
- Do not place certificate contents in Helm values or repository files.

Spans can contain repository, user, pull request, and installation context.

## Harden Kubernetes defaults

- Do not mount a service-account token unless a container needs the Kubernetes
  API.
- Keep the root filesystem read-only and bound writable temporary storage.
- Drop Linux capabilities and block privilege escalation.
- Run as a non-root user with a runtime-default seccomp profile.
- Avoid blanket namespace and pod selectors in enabled NetworkPolicy rules.
- Limit ingress to expected application, ingress, and monitoring peers.
- Limit egress to DNS, HTTPS, required application peers, and the collector.

Kubernetes NetworkPolicy cannot restrict TCP 443 by hostname. Document that
residual risk and use a capable CNI or egress gateway when destinations must be
named.

## Keep the supply chain inspectable

- Follow the repository's pinning policy for Python packages, Actions, and
  container bases.
- Keep CodeQL, secret detection, fuzzing, dependency review, and container
  scanning active.
- If you suppress a finding, record why and set an expiry or follow-up.
- State which release signatures, checksums, attestations, and VEX documents
  actually exist.
- Pin verified chart versions and image digests during controlled promotion.

Use [Verify a release](release-verification.md) for the current artifact and
provenance commands.

## Make failures useful and safe

- Name the failed operation without printing credential values.
- Return generic internal errors to webhook callers.
- Scrub common token forms before logging exceptions.
- Preserve enough structured context to correlate a failure with a delivery.
- Remove private repository, customer, account, and cloud identifier data before
  sharing evidence.

The [operations runbook](operations.md#escalate-with-safe-evidence) lists the
useful evidence and the required redactions.

## Verify security-sensitive changes

Run focused tests for the changed boundary and the full suite. Preserve 100%
statement and branch coverage unless the repository explicitly changes that
policy.

For chart changes, also run:

- Helm lint;
- Helm unit tests;
- schema-negative cases for mutually exclusive settings;
- kubeconform on each affected feature combination; and
- an install or upgrade test when runtime wiring changes.

For documentation changes, verify every command against the current source or a
rendered artifact. Never publish a security promise that the implementation
does not enforce.
