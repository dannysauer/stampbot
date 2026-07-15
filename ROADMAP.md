# Roadmap

This roadmap describes Stampbot's direction as of 2026-07-15. It is not a
release schedule or a promise that every item will ship. GitHub issues contain
the current status and acceptance criteria for individual changes.

## What guides the work

Stampbot should make a narrow decision and make it predictably: whether a
GitHub pull request matches the repository's approval policy. Reliability and
security take priority over adding more ways to approve a change.

New work should preserve these constraints:

- Fail closed when authentication, authorization, policy, or required evidence
  is incomplete
- Keep GitHub App permissions and deployment access as narrow as practical
- Expose enough telemetry to diagnose a delivery without exposing credentials
  or private repository data
- Keep configuration changes compatible or provide a clear migration
- Verify the container and Helm chart as independently versioned artifacts
- Avoid fixed account details when a repository-relative link or configurable
  publication target will work

## Active themes

### Finish the security audit work

The [security audit remediation epic
(#145)](https://github.com/dannysauer/stampbot/issues/145) and the
[security review tracker
(#236)](https://github.com/dannysauer/stampbot/issues/236) remain the umbrella
for application and deployment findings. That program covers public setup and
metrics surfaces. It also bounds attacker-influenced work and hardens
deployment defaults. Each remaining finding must be fixed or accepted with a
written rationale.

The [OpenSSF Scorecard findings
(#267)](https://github.com/dannysauer/stampbot/issues/267) have their own
tracker. The goal is to fix a finding where the project can do so without
weakening release provenance. Any exception should state the trade-off.

The [historical image policy
(#276)](https://github.com/dannysauer/stampbot/issues/276) tracks vulnerable
images that were already published. A source update fixes future builds; it
doesn't rewrite an old digest.

### Finish historical release cleanup

The app and chart publishers are serialized. Recovery pair `v1.11.8` and
`chart-v0.13.11`, plus the first subsequent automatic pair `v1.11.9` and
`chart-v0.13.12`, verified end to end. The [release cleanup and policy issue
(#273)](https://github.com/dannysauer/stampbot/issues/273) remains open for the
historical draft inventory, the chart `0.13.10` GitHub and OCI mismatch, and
tag-bound provenance, signed-tag, and separate image-signature policy.

### Make webhook handling timely and observable

The [performance audit epic
(#146)](https://github.com/dannysauer/stampbot/issues/146) tracks work that
reduces GitHub API latency and load. Connection reuse and caching are part of
that work. The [webhook acknowledgment issue
(#190)](https://github.com/dannysauer/stampbot/issues/190) covers accepting a
valid delivery before slow downstream work causes GitHub to time out.

This work must preserve idempotency. Operators also need to trace one delivery
from acceptance through its final approval, dismissal, or failure.

### Reduce structural maintenance cost

The [structural refactoring epic
(#162)](https://github.com/dannysauer/stampbot/issues/162) targets repeated API
scaffolding, oversized handlers, and loose event types. These changes should
preserve public behavior. Tests and docs must change in the same pull request if
a public contract does change.

### Publish durable documentation

The [versioned documentation issue
(#266)](https://github.com/dannysauer/stampbot/issues/266) tracks a Read the
Docs site. The repository documentation must remain useful on GitHub and build
without private assets. It should avoid account-specific paths where possible.
Hosted documentation should fail its build on warnings and broken internal
links.

### Decide the Google publishing path

Google publishing is disabled while its billing and operational ownership are
under review. The [Google publishing decision
(#262)](https://github.com/dannysauer/stampbot/issues/262) records the work
without exposing cloud account details. The result may be a verified deployment
or removal of that publishing path.

## Ongoing maintenance

The project will continue to update supported runtimes and application
dependencies. Workflow actions and base images are updated too. Release
verification must stay aligned with the artifacts that each release actually
publishes.

The [OpenSSF Best Practices Silver issue
(#133)](https://github.com/dannysauer/stampbot/issues/133) tracks the badge
work. Criteria that do not fit a single-maintainer project should be marked
accurately, not satisfied by a claim the project cannot support.

## Product boundaries

Stampbot does not aim to:

- Merge pull requests
- Grant repository access
- Bypass GitHub branch protection or repository rules
- Act as a native `CODEOWNERS` identity
- Replace human review when a repository requires human judgment

These boundaries keep the GitHub review that Stampbot submits from becoming a
broader source-control authority.

## Propose a direction

Open a [GitHub issue](https://github.com/dannysauer/stampbot/issues). Describe
the problem, who has it, and the outcome you expect. Include compatibility,
security, and operational costs that the project would take on.
