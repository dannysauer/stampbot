# Stampbot

<p align="center">
  <img src="stampbot.png" alt="Stampbot logo" width="200">
</p>

[![CI](https://github.com/dannysauer/stampbot/workflows/CI/badge.svg)](https://github.com/dannysauer/stampbot/actions/workflows/ci.yml)
[![Release](https://github.com/dannysauer/stampbot/workflows/Release/badge.svg)](https://github.com/dannysauer/stampbot/actions/workflows/release.yml)
[![codecov](https://codecov.io/gh/dannysauer/stampbot/branch/main/graph/badge.svg)](https://codecov.io/gh/dannysauer/stampbot)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/dannysauer/stampbot/badge)](https://scorecard.dev/viewer/?uri=github.com/dannysauer/stampbot)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/12925/badge)](https://www.bestpractices.dev/projects/12925)
[![Mutation Score](https://dannysauer.github.io/stampbot/mutation/mutation-badge.svg)](https://dannysauer.github.io/stampbot/mutation/mutation-report.html)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-yellow.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Helm](https://img.shields.io/badge/helm-v3-blue.svg)](https://helm.sh)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://hub.docker.com)

Stampbot is a GitHub App that approves pull requests when repository policy says
it may. A label or an authorized `@stampbot` comment can create an approval.
Stampbot can withdraw its own approval too.

It never merges a pull request, changes branch protection, grants access, or
reviews as a person. That boundary is the point.

Visit the [Stampbot project site](https://stampbot.github.io/) for the short
version, or keep reading for a working setup.

## Choose when Stampbot may approve

Policy lives in `stampbot.toml` on the repository's default branch. This small
policy approves pull requests carrying the `autoapprove` label:

```toml
approval_labels = ["autoapprove"]
reapprove = false
chatops_required_permission = "maintain"
```

Label approval can also require another label, a matching title, a named author,
or membership in an organization team. Every configured filter category must
pass. Values inside a category are alternatives.

ChatOps follows a separate rule. A commenter with the configured repository
permission can write `@stampbot approve` or `@stampbot unapprove`; label filters
don't apply to that command.

Removing an approval label dismisses Stampbot's active reviews. A new commit
doesn't receive another approval unless `reapprove = true` or an authorized
maintainer asks for one.

The [configuration reference](docs/configuration.md) lists every key, limit,
default, and fallback.

## Try it from source

Install Git, Make, Poetry, and Python 3.11 or newer. Then run:

```bash
git clone https://github.com/dannysauer/stampbot.git
cd stampbot
make install-dev
STAMPBOT_SETUP_ENABLED=true \
STAMPBOT_BASE_URL=http://localhost:8000 \
make dev
```

Check the process from another terminal:

```bash
curl -fsS http://127.0.0.1:8000/health
```

It should return:

```json
{"status":"healthy"}
```

Open <http://127.0.0.1:8000/setup> to create a development GitHub App. The
wizard is off by default and uses only the `STAMPBOT_BASE_URL` you supplied.
Request headers can't change its callback or webhook destination.

GitHub can't deliver a webhook to localhost. Before testing a real pull request,
put port 8000 behind a public HTTPS tunnel and restart Stampbot with that origin
as `STAMPBOT_BASE_URL`. The [installation guide](INSTALLATION.md) walks through
the App permissions, credentials, and production runtimes.

## Pick a runtime

| Goal | Guide |
| --- | --- |
| Run from source or a container | [Install Stampbot](INSTALLATION.md) |
| Run on Kubernetes | [Install the Helm chart](charts/stampbot/README.md) |
| Deploy through the repository's Google Cloud workflow | [Deploy to Cloud Run](docs/deploy-gcp-cloudrun.md) |
| Promote a signed image or chart | [Verify a release](docs/release-verification.md) |

The app exposes liveness and readiness, plus structured logs and optional
OpenTelemetry traces. Operators can enable Prometheus metrics on a separate
listener. Start with the
[runbook](docs/operations.md).

## Understand the boundary

GitHub sends a signed webhook. Stampbot verifies the signature before parsing
the body, loads policy from GitHub, and acts through the App installation that
received the event. GitHub remains the source of truth for review state.

Stampbot is not a native code owner. GitHub Apps can't appear in `CODEOWNERS`.
For path-level bot ownership, use
[Extra CODEOWNERS](https://github.com/stampbot/extra-codeowners) and read its
[safety boundary](https://extra-codeowners.readthedocs.io/en/latest/reference/checks/#eventual-consistency)
before replacing GitHub's native code-owner review rule.

The [architecture](docs/architecture.md) traces the complete request path. The
[security requirements](docs/security-requirements.md) state the properties a
change must preserve.

## Find the rest

The [documentation index](docs/README.md) routes readers by task. These files
cover project work:

- [contributing](CONTRIBUTING.md) explains local checks and pull requests;
- [security](SECURITY.md) explains private vulnerability reporting;
- [governance](GOVERNANCE.md) records ownership and decisions; and
- [roadmap](ROADMAP.md) records planned work.

Stampbot is licensed under the [Apache License 2.0](LICENSE).
