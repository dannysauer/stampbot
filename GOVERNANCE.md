# Governance

Stampbot is maintained as a small personal open source project.

## Maintainer

The current maintainer is Danny Sauer (`@dannysauer`). The maintainer is responsible for:

- reviewing and merging pull requests
- triaging bugs, enhancement requests, and vulnerability reports
- maintaining release automation, dependencies, and deployment documentation
- deciding project scope and compatibility policy

## Decision Making

Project decisions are made in public GitHub Issues and pull requests when possible. The
maintainer decides whether a change is in scope, whether its implementation is acceptable,
and whether it is ready to merge.

Security-sensitive decisions may be discussed privately through GitHub Security Advisories
until disclosure is appropriate.

## Access Control

Repository and release access is limited to the maintainer and automation explicitly
configured in this repository. GitHub branch rules, required checks, CodeQL, secret
scanning, dependency monitoring, release signing, and Scorecard run as repository controls.

## Continuity

Stampbot is currently a single-maintainer project. The project is published under the
Apache-2.0 license, so users may fork and maintain their own copy if the upstream project
becomes unavailable. Formal multi-person access continuity is not currently guaranteed.

## Changing Governance

Governance changes should be proposed in a GitHub Issue or pull request. Changes that add
maintainers or broaden release access should document the new responsibilities and access
controls before access is granted.
