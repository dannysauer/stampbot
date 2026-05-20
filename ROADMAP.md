# Roadmap

This roadmap documents current project direction. It is not a promise to deliver every
item, but it gives contributors a shared view of likely priorities.

## Current Priorities

- Keep Stampbot reliable for label-based and ChatOps pull request approvals.
- Maintain a secure supply chain through pinned dependencies, automated dependency
  updates, CodeQL, container scanning, SBOMs, VEX, release signing, SLSA provenance, and
  OpenSSF Scorecard.
- Keep the Helm chart and container deployment paths working for Kubernetes users.
- Improve documentation for installation, operations, security posture, and contribution
  expectations.

## Near-Term Work

- Continue pursuing the OpenSSF Best Practices Silver badge where the criteria accurately
  fit a single-maintainer project.
- Verify the first releases that include SLSA provenance and update release verification
  instructions if needed.
- Keep mutation and fuzzing checks useful without making the CI feedback loop
  unnecessarily slow.

## Out of Scope

- Stampbot does not merge pull requests.
- Stampbot does not grant repository permissions or bypass GitHub branch protection.
- Stampbot does not replace human code review for projects that require it.

## Proposing Changes

Use GitHub Issues for roadmap proposals. Include the problem, expected user impact, and any
security or compatibility considerations.
