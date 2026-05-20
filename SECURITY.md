# Security Policy

## Supported Versions

Stampbot is released continuously from `main`. Security fixes are provided in the latest
release only.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately through GitHub Security Advisories:

https://github.com/dannysauer/stampbot/security/advisories/new

Do not open a public issue for suspected vulnerabilities. Include the affected version,
steps to reproduce, impact, and any suggested remediation when possible.

## Security Checks

This repository uses GitHub CodeQL, Trivy container scanning, Dependabot alerts, secret
scanning with push protection, OpenSSF Scorecard, release signing, SBOMs, VEX documents,
and SLSA provenance for new release artifacts.

Security requirements are documented in [docs/security-requirements.md](docs/security-requirements.md).
Release verification is documented in [docs/release-verification.md](docs/release-verification.md).
