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

A GitHub App that automatically approves pull requests based on labels and chatops commands.

Stampbot solves a narrow workflow problem for repositories that intentionally allow a
trusted label or maintainer ChatOps command to satisfy pull request approval requirements.
It watches GitHub pull request webhooks, checks repository-specific policy, and creates or
dismisses Stampbot's own pull request approval review.

## Features

- **Label-based Auto-Approval**: Automatically approve PRs when specific labels are added
- **ChatOps Support**: Approve or unapprove PRs via `@stampbot approve` or `@stampbot unapprove` comments (permission required), and show repo-specific help with `@stampbot help`
- **PR Eligibility Filters**: Restrict auto-approval to PRs matching required labels, title patterns, allowed users, or allowed teams
- **Configurable**: Per-repository configuration via `stampbot.toml`
- **Fully Instrumented**: OpenTelemetry support for distributed tracing
- **Prometheus Metrics**: Comprehensive metrics for monitoring
- **Deployment options**:
  - Kubernetes deployment with Helm chart
  - Horizontal Pod Autoscaler (HPA) with custom metrics support
  - Vertical Pod Autoscaler (VPA) support
  - AWS Secrets Manager integration for EKS
  - Pod Disruption Budgets
  - Network Policies
- **CI/CD**:
  - Conventional commits and branches
  - PR-tagged container images
  - Automated releases

## Easy Setup (Recommended)

Stampbot includes a built-in setup wizard that creates your GitHub App automatically:

1. **Start stampbot without credentials**
   ```bash
   make install-dev
   make dev
   ```

2. **Open the setup page**
   Visit http://localhost:8000 - you'll be automatically redirected to the setup wizard

3. **Create your GitHub App**
   Click "Create GitHub App" and follow the prompts on GitHub.
   Stampbot includes the detected webhook URL in the manifest. If you are running behind
   a tunnel or proxy, set `STAMPBOT_BASE_URL` to the public origin before opening setup.

4. **Save your credentials**
   Copy the displayed credentials to your `.env` file

5. **Restart stampbot**
   ```bash
   make dev
   ```

6. **Install the app**
   Install your new GitHub App on the repositories you want to use

For manual setup, production deployment, and the full permission table, see
[INSTALLATION.md](INSTALLATION.md).

## Project Information

- **Obtain Stampbot**: Clone this repository, build the Docker image, or install the Helm
  chart from GitHub Container Registry. See [Quick Start](#quick-start) and
  [INSTALLATION.md](INSTALLATION.md).
- **Report bugs or request enhancements**: Open a public
  [GitHub Issue](https://github.com/dannysauer/stampbot/issues). Report suspected
  vulnerabilities privately using [SECURITY.md](SECURITY.md).
- **Contribute changes**: Follow [CONTRIBUTING.md](CONTRIBUTING.md) for contribution
  requirements, coding standards, commit format, and pull request expectations.
- **Governance and roadmap**: See [GOVERNANCE.md](GOVERNANCE.md) for project roles and
  decision making, and [ROADMAP.md](ROADMAP.md) for current direction.
- **Documentation index**: See [docs/README.md](docs/README.md) for the GitHub-native
  documentation map.
- **Configuration reference**: See [docs/configuration.md](docs/configuration.md) for
  app settings, `stampbot.toml`, GitHub App permissions, events, defaults, and failure
  behavior.
- **External interface reference**: See [docs/reference.md](docs/reference.md) for HTTP
  endpoints, webhook inputs, ChatOps commands, GitHub outputs, and metrics.
- **Operations runbook**: See [docs/operations.md](docs/operations.md) for webhook
  triage, permission failures, metrics, rollback, and escalation data.
- **Architecture and security requirements**: See [docs/architecture.md](docs/architecture.md)
  and [docs/security-requirements.md](docs/security-requirements.md).
- **Release verification**: See [docs/release-verification.md](docs/release-verification.md)
  for Sigstore bundle and SLSA provenance verification.
- **Language**: Project documentation, issue discussion, code review, and maintainer
  responses are in English.

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for containerized deployment)
- Kubernetes cluster (for production deployment)
- Helm 3+ (for Kubernetes deployment)

### Local Development

1. Clone the repository:
```bash
git clone https://github.com/dannysauer/stampbot.git
cd stampbot
```

2. Install dependencies:
```bash
make install-dev
```

3. Start stampbot (it will guide you through GitHub App setup):
```bash
make dev
```

4. Open http://localhost:8000 and follow the setup wizard

### Docker

Build and run with Docker:

```bash
make docker-build
docker run -p 8000:8000 --env-file .env stampbot:latest
```

### Kubernetes

Deploy with Helm:

```bash
kubectl create namespace stampbot
kubectl create secret generic stampbot-github \
  --namespace stampbot \
  --from-literal=STAMPBOT_APP_ID=YOUR_APP_ID \
  --from-file=STAMPBOT_PRIVATE_KEY=./private-key.pem \
  --from-literal=STAMPBOT_WEBHOOK_SECRET=YOUR_WEBHOOK_SECRET

helm install stampbot charts/stampbot \
  --namespace stampbot \
  --set github.existingSecret=stampbot-github
```

For detailed installation instructions and chart values, see [INSTALLATION.md](INSTALLATION.md) and
[charts/stampbot/README.md](charts/stampbot/README.md).

## Configuration

### Repository Configuration

Create a `stampbot.toml` file in the root of your repository:

```toml
# Labels that trigger auto-approval
approval_labels = ["autoapprove", "stamp", "ready-to-merge"]

# Auto-approve when label is added (default: true)
auto_approve_on_label = true

# Re-approve when new commits are pushed and an approval label still applies
# (default: false)
reapprove = false

# Enable chatops commands (default: true)
chatops_enabled = true

# Minimum repo permission required for chatops (default: "maintain")
# Valid values: "none", "read", "triage", "write", "maintain", "admin"
chatops_required_permission = "maintain"

# Commands that trigger approval
approve_commands = ["approve", "stamp"]

# Commands that dismiss approvals
unapprove_commands = ["unapprove", "unstamp"]

# --- PR Eligibility Filters ---
# All configured filters must pass (AND logic between filter types).
# Within each filter, any single match is sufficient (OR logic).
# Omit or leave empty to disable that filter.

# PR must have at least one of these labels to be eligible for auto-approval
required_labels = ["autoapprove"]

# PR title must match at least one of these regex patterns to be eligible
required_title_patterns = ["^feat:", "^fix:"]

# PR author (GitHub login) must be in this list to be eligible
allowed_users = ["bot-user", "trusted-contributor"]

# PR author must be a member of at least one of these teams to be eligible
# Format: "org/team-slug" or just "team-slug"
allowed_teams = ["my-org/release-team"]
```

Stampbot loads `stampbot.toml` from the repository's default branch. If the file
is missing and the repository belongs to an organization, it will also check
the org-wide `.github` repository for `stampbot.toml`.

### Application Configuration

Configure the app via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `STAMPBOT_APP_ID` | GitHub App ID | - (use /setup) |
| `STAMPBOT_PRIVATE_KEY` | GitHub App private key or path | - (use /setup) |
| `STAMPBOT_WEBHOOK_SECRET` | Webhook secret | - (use /setup) |
| `STAMPBOT_SETUP_ENABLED` | Enable /setup endpoint | `true` |
| `STAMPBOT_BASE_URL` | Public base URL for setup callback and webhook URL generation | auto-detected |
| `STAMPBOT_HOST` | Host used by `python -m stampbot` | `0.0.0.0` |
| `STAMPBOT_PORT` | Port used by `python -m stampbot` | `8000` |
| `STAMPBOT_LOG_LEVEL` | Logging level | `INFO` |
| `STAMPBOT_LOG_FORMAT` | `json`, `console`, or `auto` | `auto` |
| `STAMPBOT_CLIENT_IP_HEADER` | Header used for logged client IP | `X-Forwarded-For` |
| `STAMPBOT_OTEL_ENABLED` | Enable OpenTelemetry | `false` |
| `STAMPBOT_OTEL_ENDPOINT` | OTLP endpoint | - |
| `STAMPBOT_OTEL_SERVICE_NAME` | OpenTelemetry service name | `stampbot` |

Stampbot uses Dynaconf for configuration. In order of precedence it reads:
environment variables (`STAMPBOT_*`), `.secrets.toml`, `settings.toml`, and `.env`
(use `.env` only for local development).

**Note:** If GitHub App credentials are not configured, stampbot runs in setup mode
and redirects to `/setup` where you can create your GitHub App automatically.
Disable `STAMPBOT_SETUP_ENABLED` after production setup.

See [docs/configuration.md](docs/configuration.md) for every app setting, repository
configuration key, validation rule, permission, event subscription, and failure mode.

## Usage

### Label-based Approval

1. Add an approval label (e.g., `autoapprove`) to a PR
2. Stampbot automatically approves the PR
3. Remove the label to dismiss the approval

By default, Stampbot does not approve again when new commits are pushed after
approval. Set `reapprove = true` to opt in to re-approval on new commits while
an approval label still applies.

### ChatOps Commands

Comment on a PR with:

- `@stampbot approve` or `@stampbot stamp` - Approve the PR
- `@stampbot unapprove` or `@stampbot unstamp` - Dismiss approval
- `@stampbot help` - Show available commands and configured approval labels

Approval and unapproval commands require the configured repository permission.
By default, this is set to `maintain` and can be configured per repo.

## Metrics

Stampbot exposes Prometheus metrics at `/metrics` on the main HTTP port (default 8000):

- `stampbot_http_requests_total` - Total HTTP requests
- `stampbot_webhook_events_total` - Webhook events received
- `stampbot_pr_approvals_total` - PR approvals by trigger type
- `stampbot_errors_total` - Errors by type
- `stampbot_github_api_requests_total` - GitHub API requests
- `stampbot_github_api_rate_limit_remaining` - GitHub API rate limit

## Development

### Running Tests

```bash
make test
```

### Linting

```bash
make lint
```

### Formatting

```bash
make format
```

### Secret Detection

We use [detect-secrets](https://github.com/Yelp/detect-secrets) to prevent accidental secret commits. False positives are tracked in `.secrets.baseline`.

To update the baseline when adding intentional test secrets:

```bash
make secrets-baseline
git add .secrets.baseline
```

To audit the baseline and mark false positives:

```bash
.venv/bin/detect-secrets audit .secrets.baseline
```

## Architecture

Stampbot is built with:

- **FastAPI**: Modern, fast web framework
- **PyGithub**: GitHub API client
- **Dynaconf**: Configuration management
- **OpenTelemetry**: Distributed tracing
- **Prometheus**: Metrics collection
- **Structlog**: Structured logging

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for acceptable contributions, coding standards,
required checks, commit format, and pull request expectations.

## CI/CD

The project uses GitHub Actions for CI/CD:

- **CI**: Runs on every PR and push to main/develop
- **PR Images**: Builds tagged images for each PR
- **Release**: Automatic releases on version tags

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/dannysauer/stampbot/issues)
- **Documentation**: [docs/](docs/)

## Acknowledgments

Built with inspiration from the Kubernetes community and GitHub Apps ecosystem
