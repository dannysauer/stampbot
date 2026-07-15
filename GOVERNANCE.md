# Governance

Stampbot uses a maintainer-led model. Contributors can shape decisions in
issues and pull requests, while the maintainer remains accountable for the
project's scope, releases, and access.

## Roles

### Maintainer

Danny Sauer ([@dannysauer](https://github.com/dannysauer)) is the current
maintainer. The maintainer:

- Sets project scope and compatibility policy
- Triages issues and private vulnerability reports
- Reviews and integrates changes
- Manages releases, repository settings, and automation access
- Enforces the [code of conduct](CODE_OF_CONDUCT.md)

### Contributors

A contributor helps improve Stampbot without holding maintainer authority.
Reporting a problem or submitting any project change counts. Contribution does
not grant repository or release access.

### Automation

Repository automation runs checks, updates dependencies, and creates releases.
An automation identity is not a maintainer. Its permissions should cover only
the declared job it performs.

## How decisions are made

Make public decisions in a GitHub issue or pull request. State the user problem
and proposed outcome. Cover compatibility, security, and long-term maintenance
costs. The maintainer weighs that evidence and makes the final decision.

Open an issue before a substantial change to a public contract or project
governance. Public contracts include configuration, GitHub App permissions,
and deployment behavior. Routine fixes can go straight to a focused pull
request when the problem and solution are clear.

Security work may remain in a private GitHub Security Advisory until disclosure
is safe. The [security policy](SECURITY.md) explains that process.

A decision can change when new evidence appears. Record the new reasoning in
the issue or pull request so future contributors don't have to reconstruct it
from commit history.

## Reviews and integration

Required checks must pass before a change reaches `main`. The maintainer checks
that the implementation, tests, and public docs agree. Review also covers
compatibility and security impact.

Stampbot keeps a linear history. The maintainer generally fast-forwards a small
branch when its commits are clean and signed. Squash merge is the fallback for
a branch that needs consolidation. Merge commits are disabled.

The project currently has one human maintainer, so it cannot promise an
independent approval on every change. Repository rules and automated checks
reduce accidental changes, but they are not a substitute for another reviewer.

## Maintainer access

Repository and release access stays limited to the maintainer and the
automation declared in this repository. A change that grants a person or
automation identity new access must document:

- The responsibility that needs the access
- The minimum repository, package, or deployment permissions
- Who can revoke or recover the access
- Which credentials need rotation
- Where the change leaves an audit record

Maintainer status is not earned by reaching a contribution count. The current
maintainer may invite someone who has shown sustained judgment and understands
the security boundary. The candidate must also agree to own a defined part of
the project. Record the role and access change in a governance pull request
before granting access.

## Disagreements and conduct

Keep technical disagreements on the issue or pull request. Summarize the points
of disagreement and the evidence for each option. The maintainer decides when
the project needs a conclusion.

Follow the [code of conduct](CODE_OF_CONDUCT.md) for behavior concerns. There is
no independent project escalation path while Stampbot has one maintainer. A
participant may use GitHub's platform reporting and support channels when the
concern involves account or platform abuse.

## Continuity

There is no guaranteed response time or release schedule. The project also
lacks multi-person continuity today. If the maintainer becomes unavailable,
the [Apache License 2.0](LICENSE) permits the community to fork and maintain
Stampbot.

A repository transfer does not change this governance model by itself. Any
change in project authority must update this file.

## Amend this policy

Propose a governance change in an issue or pull request. Explain the problem
with the current model and the authority or responsibility that would change.
