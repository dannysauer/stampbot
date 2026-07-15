# Governance

Stampbot is a small open source project with one maintainer.

## Maintainer

Danny Sauer ([@dannysauer](https://github.com/dannysauer)) maintains the
project. The maintainer:

- reviews and merges changes;
- triages bugs, proposals, and private vulnerability reports;
- owns release and repository access;
- maintains automation and dependencies; and
- decides scope and compatibility.

## How decisions are made

Use a GitHub issue or pull request for decisions that can be public. State the
problem, affected readers, compatibility cost, and security impact.

The maintainer decides whether a proposal fits Stampbot and whether its
implementation is ready. Security work may stay in a private advisory until
disclosure is safe.

## Access and continuity

Repository and release access is limited to the maintainer and the automation
declared in this repository. Branch rules, required checks, secret scanning,
dependency monitoring, and release controls reduce accidental changes; they
don't create a second human maintainer.

There is no guaranteed multi-person continuity today. The Apache-2.0 license
lets users fork and maintain Stampbot if the upstream project becomes
unavailable.

## Change this model

Propose governance changes in an issue or pull request. A proposal that adds a
maintainer or release principal must name the responsibility, access being
granted, recovery path, and audit controls before access changes.
