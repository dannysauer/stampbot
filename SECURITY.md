# Security policy

## Supported releases

Stampbot fixes security defects in the latest release. Older app and chart
versions don't receive separate backports.

## Report a vulnerability

Use a private
[GitHub Security Advisory](https://github.com/dannysauer/stampbot/security/advisories/new).
Don't open a public issue.

Include what you can verify:

- affected app or chart version;
- deployment mode;
- prerequisites and reproduction steps;
- security impact and exposed boundary; and
- a suggested fix or mitigation, if you have one.

Remove real tokens, private keys, webhook secrets, customer data, and private
repository content. A minimal synthetic reproduction is preferable.

The project doesn't promise a fixed response or disclosure time. The maintainer
will use the private advisory to confirm impact and coordinate a safe public
release.

## Security posture

The [security requirements](docs/security-requirements.md) describe the
properties code changes must preserve. CI also runs CodeQL, secret detection,
fuzzing, dependency checks, OpenSSF Scorecard, and container scanning.

Published signatures and attestations vary by release. Check the actual asset
list and follow [Verify a Stampbot release](docs/release-verification.md)
instead of assuming an artifact exists.
