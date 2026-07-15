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

Stampbot is a GitHub App that turns a configured label or an authorized ChatOps
comment into a pull request approval. It can also withdraw its own approval.

Stampbot does one job. It doesn't merge pull requests, change branch protection, or
approve as a human reviewer.

## How it works

GitHub sends Stampbot a signed webhook whenever a relevant pull request changes.
Stampbot verifies the signature, reads the repository's policy, and acts as that
GitHub App installation.

A repository can approve by label:

1. Add a label listed in `approval_labels`.
2. Stampbot checks any label, title, user, and team filters.
3. If every configured filter passes, Stampbot adds its approval.

Or a maintainer can leave `@stampbot approve` on the pull request. ChatOps commands
have their own repository-permission threshold and don't use the label eligibility
filters.

Removing any configured approval label dismisses Stampbot's active approvals.
Approvals aren't refreshed after a new commit unless `reapprove = true` or an
authorized maintainer asks Stampbot to approve again.

> **Stampbot is not a native code owner.** GitHub Apps can't appear in
> `CODEOWNERS`. If you need path-level bot ownership, use
> [Extra CODEOWNERS](https://github.com/stampbot/extra-codeowners) and read its
> [current safety boundary](https://extra-codeowners.readthedocs.io/en/latest/reference/checks/#eventual-consistency)
> before replacing GitHub's native code-owner rule.

## Try Stampbot locally

You need Git, Make, Poetry, and Python 3.11 or newer. The repository's Makefile
creates `.venv/` and keeps all Python tools inside it.

```bash
git clone https://github.com/dannysauer/stampbot.git
cd stampbot
make install-dev
make dev
```

In another terminal, check the process:

```bash
curl http://127.0.0.1:8000/health
```

The response is:

```json
{"status":"healthy"}
```

Open <http://127.0.0.1:8000> to create a GitHub App with the setup wizard. A
`localhost` webhook URL isn't reachable from GitHub. For a working local webhook,
expose port 8000 through a public HTTPS tunnel and set `STAMPBOT_BASE_URL` to that
public origin before you open the wizard.

Once the App is installed and GitHub can reach `/webhook`, add this file to the
default branch of a test repository:

```toml
# stampbot.toml
approval_labels = ["autoapprove"]
reapprove = false
chatops_required_permission = "maintain"
```

Open a pull request and add the `autoapprove` label. A successful run leaves an
approval review from your Stampbot App on the pull request.

For production credentials and every deployment path, use the
[installation guide](INSTALLATION.md).

## What you can configure

Repository policy lives in `stampbot.toml`. Each repository can choose:

- labels that create or dismiss approval;
- commands and the permission needed to run them;
- whether a new commit can receive a fresh approval;
- required labels or title patterns; and
- allowed users or organization teams.

Stampbot first checks `stampbot.toml` on the target repository's default branch.
For an organization repository, it then checks `ORG/.github`. If neither file
exists, the service defaults apply.

See the [configuration reference](docs/configuration.md) for every key, default,
validation rule, permission, and fallback.

## Run it where you need it

| If you want to… | Start here |
| --- | --- |
| Run from a source checkout or container | [Install Stampbot](INSTALLATION.md) |
| Install the published Helm chart | [Stampbot Helm chart](charts/stampbot/README.md) |
| Deploy with the repository's Cloud Run workflow | [Deploy to Cloud Run](docs/deploy-gcp-cloudrun.md) |
| Verify a release before deployment | [Verify a release](docs/release-verification.md) |

The app exposes liveness, readiness, Prometheus metrics, structured logs, and
optional OpenTelemetry traces. Operators should start with the
[runbook](docs/operations.md).

## Find the right document

The [documentation index](docs/README.md) groups pages by task. Common entry
points are:

- [interface reference](docs/reference.md) for HTTP routes, webhooks, ChatOps, and metrics;
- [architecture](docs/architecture.md) for the request path and trust boundaries;
- [security requirements](docs/security-requirements.md) for the properties this project protects;
- [contributing guide](CONTRIBUTING.md) for local checks and pull request expectations; and
- [security policy](SECURITY.md) for private vulnerability reports.

Project direction and ownership are in [ROADMAP.md](ROADMAP.md) and
[GOVERNANCE.md](GOVERNANCE.md).

## License

Stampbot is available under the [Apache License 2.0](LICENSE).
