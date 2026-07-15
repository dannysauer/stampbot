# Security policy

## Supported releases

Stampbot maintains two release lines. The project versions them independently.

| Release line | Supported version |
| --- | --- |
| Application and container | Latest non-prerelease `vX.Y.Z` release |
| Helm chart | Latest non-prerelease `chart-vX.Y.Z` release |

The maintainer does not routinely backport security fixes to older application
or chart versions. The `main` branch and pull request images are development
outputs. Draft releases and prerelease artifacts are not supported releases.

## Report a vulnerability privately

Open a private
[GitHub Security Advisory](https://github.com/dannysauer/stampbot/security/advisories/new).
Do not open a public issue or pull request for a vulnerability that has not been
disclosed.

Test only systems and repositories you own or have permission to assess. Use a
synthetic repository and synthetic data when you can.

Include the evidence you have:

- The affected application, container, or chart version
- The deployment mode and relevant security boundary
- The prerequisites and smallest reliable reproduction
- The impact an attacker can cause
- Whether the issue is being exploited
- A proposed mitigation or fix, if you have one

Remove credentials and authentication material, including tokens, private keys,
webhook secrets, and cookies. Remove customer data, private repository content,
and cloud account identifiers too. Do not attach an unsanitized webhook payload
or log export. Sanitize configuration files and deployment manifests as well.
The maintainer can ask for a narrower artifact through the private advisory if
it is needed.

## What happens after a report

The maintainer uses the private advisory to reproduce the issue and assess its
impact. Remediation may change the application or deployment defaults. It may
also require documentation or credential rotation guidance.

Stampbot does not promise a fixed response or disclosure time. Disclosure
depends on the impact, the availability of a mitigation, and any coordination
needed with an upstream project. The maintainer will keep exploit details in
the advisory until public disclosure is appropriate.

Tell the maintainer whether you want public credit and which name or profile to
use. When a report leads to a published advisory, Stampbot credits the reporter
as requested. The advisory omits attribution when the reporter requests
anonymity.

## Security boundaries

Security reports are especially useful when they show that Stampbot can:

- Accept a webhook without a valid signature
- Approve or dismiss a review without the required policy or actor permission
- Expose GitHub App credentials, webhook secrets, or private repository data
- Let an untrusted caller take over setup or read protected operational data
- Perform unbounded work from attacker-controlled input
- Publish an artifact that cannot be tied to the claimed source and release

This list is not exhaustive. Report a plausible boundary failure privately even
when you are unsure whether it is exploitable.

The [security requirements](docs/security-requirements.md) define the properties
that code and deployment changes must preserve. They are engineering
requirements, not a claim that automated checks prove the software secure.

## Automated checks and release evidence

The repository runs several automated checks:

- CodeQL static analysis
- Container vulnerability and dependency monitoring
- Secret detection
- Fuzzing
- OpenSSF Scorecard analysis

Findings may still need human triage. A passing run does not replace a
vulnerability report.

Release signatures, attestations, and provenance can vary by artifact. Inspect
the release's actual asset list and follow
[Verify a Stampbot release](docs/release-verification.md) before trusting an
artifact.
