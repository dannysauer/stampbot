# AGENTS.md

This file provides guidance for AI agents (Claude Code, ChatGPT, Copilot, etc.) working on this repository.

## Project Overview

Stampbot is a GitHub App that automatically approves pull requests based on labels and ChatOps commands. Built with Python 3.11+, FastAPI, and deployed on Kubernetes.

## Technology Stack

- **Language**: Python 3.11+
- **Web Framework**: FastAPI
- **GitHub Integration**: PyGithub
- **Configuration**: Dynaconf, Pydantic Settings
- **Logging**: Structlog
- **Metrics**: Prometheus Client
- **Tracing**: OpenTelemetry
- **Containerization**: Docker
- **Orchestration**: Kubernetes via Helm
- **CI/CD**: GitHub Actions

## Common Commands

All local development uses a virtual environment in `.venv/`. The Makefile automatically creates and uses it.

```bash
# Development (venv auto-created)
make install-dev    # Install dependencies (dev mode)
make dev            # Run with hot-reload (uvicorn --reload)
make lint           # Run ruff + mypy
make format         # Format with ruff format + ruff --fix
make test           # Run pytest with coverage

# Run single test
.venv/bin/pytest tests/test_webhook_handler.py::test_function_name -v

# Pre-commit hooks
make pre-commit-install  # Install git hooks (run once)
make pre-commit          # Run all pre-commit checks manually

# Local GitHub Actions testing (requires: docker, act)
make act-lint       # Run lint job locally
make act-test       # Run test job locally
make act-ci         # Run full CI workflow locally

# Docker/Kubernetes (no venv needed)
make docker-build   # Build multi-arch image
make helm-lint      # Lint Helm chart
make helm-install   # Install chart locally

# Cleanup (removes venv)
make clean
```

## Architecture

**Request flow:**
```
GitHub Webhook -> POST /webhook -> WebhookHandler.handle_event()
                                        |
                        +---------------+---------------+
                        |               |               |
                        v               v               v
                pull_request     issue_comment        ping
                (label-based)     (ChatOps)       (health)
                        |               |
                        v               v
                GitHubAppClient (JWT + installation token auth)
                        |
                        v
                approve_pr() / dismiss_approval()
```

**Core modules in `stampbot/`:**
- `main.py` - FastAPI app, endpoints (/webhook, /health, /metrics)
- `webhook_handler.py` - Event routing, label/chatops processing
- `github_client.py` - GitHub API with app authentication (JWT)
- `config.py` - Dynaconf-based configuration (env vars, settings.toml, per-repo stampbot.toml)
- `manifest.py` - GitHub App manifest creation for easy setup
- `metrics.py` - Prometheus metrics definitions
- `telemetry.py` - OpenTelemetry tracing setup
- `logger.py` - Structlog configuration

**Configuration hierarchy:**
1. Environment variables (STAMPBOT_*) - highest priority
2. .secrets.toml - for sensitive values (gitignored)
3. settings.toml - app defaults
4. Per-repo stampbot.toml - repo-specific overrides (merged with defaults)

## Development Guidelines

### Virtual Environment Requirement

All local development must use the virtual environment. The Makefile handles this automatically - all `make` targets that run locally (install, test, lint, format, run, dev) will create and use `.venv/`. Do not install packages globally or run tools outside the venv.

### Code Style

This project follows the [Google Python Style Guide](docs/external/google-python-style-guide.md) with the following exception:
- **Line length**: 100 characters (instead of Google's 80)

**Tooling:**
- **Ruff**: All-in-one linter and formatter enforcing Google style
  - Format: 100-char lines, double quotes, spaces, 4-space indentation
  - Lint rules: E, F, I, N, W, B, C4, UP, D (pydocstyle with Google convention)
- **MyPy**: Strict type checking (required per Google style guide section 2.21)
- **Docstrings**: Google style with Args, Returns, Raises sections

**Key Google style conventions:**
- Use type annotations for all public APIs
- Prefer small, focused functions
- Use explicit exception handling (no bare `except:`)
- Imports: one per line, grouped (stdlib, third-party, local)
- Naming: `module_name`, `ClassName`, `function_name`, `CONSTANT_NAME`

### Conventions

**Commits:** Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`)

**Branches:** `feat/*`, `fix/*`, `docs/*`, `refactor/*`, `chore/*`

### Testing

- **Framework**: pytest with pytest-asyncio
- **Coverage**: Aim for >80% coverage
- **Test Location**: `tests/` directory mirroring source structure

**Important:** Tests run in the virtual environment. Always use `make test` or run pytest from the venv:

```bash
# Run all tests (preferred)
make test

# Run specific test file
.venv/bin/pytest tests/test_webhook_handler.py -v

# Run specific test
.venv/bin/pytest tests/test_webhook_handler.py::TestClassName::test_name -v

# Never run pytest directly from system Python - it won't have dependencies
```

## Key Patterns

- Webhook signature verification uses HMAC-SHA256 constant-time comparison
- GitHub App auth: generate JWT from private key -> exchange for installation token
- Metrics use labels with low cardinality (<100 unique combinations)
- Structured logging outputs JSON in production, console in debug mode

## Common Tasks

### Adding a New Feature

1. Create a feature branch: `feat/feature-name`
2. Update relevant modules in `stampbot/`
3. Add tests in `tests/`
4. Update documentation
5. Run linting and tests: `make lint test`
6. Commit with conventional commit format
7. Create PR

### Adding Metrics

1. Define metric in `stampbot/metrics.py`
2. Use metric in relevant module
3. Document metric in README.md
4. Test metric collection

### Modifying Webhook Handling

1. Update `stampbot/webhook_handler.py`
2. Consider impact on `stampbot/github_client.py`
3. Update tests
4. Test with sample webhook payloads

### Updating Helm Chart

When modifying any Helm chart component (values, templates, helpers, or documentation under `charts/`):

1. Modify `charts/stampbot/values.yaml` or templates
2. **Required:** Run `helm lint charts/stampbot` before sending changes
3. **Required:** Run `helm template test charts/stampbot` to verify templates render correctly
4. Test deployment in test cluster if possible
5. **Do NOT** manually update `version` or `appVersion` in `Chart.yaml` - these use `0.0.0-placeholder` and are set automatically at release time
6. Document changes in README.md

**Helm testing layers:**

| Layer | Tool | Speed | What it catches |
|-------|------|-------|-----------------|
| Lint | `helm lint` | Fast | Syntax, structure |
| Template | `helm template` | Fast | Rendering errors |
| Validate | `kubeconform` | Fast | K8s schema issues |
| Unit | `helm-unittest` | Fast | Logic, conditionals |
| Install | `kind` cluster | Slow | Real deployment issues |

**Running Helm tests locally:**
```bash
# Run all Helm tests (lint + validate + unittest)
make helm-test

# Or run individual steps:
make helm-lint       # Lint the chart
make helm-template   # Render templates
make helm-validate   # Validate with kubeconform (requires kubeconform installed)
make helm-unittest   # Run unit tests via Docker (no plugin install needed)
```

**Unit tests:** Located in `charts/stampbot/tests/`. Each `*_test.yaml` file tests a specific template. When adding new templates or modifying existing ones, update/add corresponding tests.

**Pre-commit hooks:** The `helm-kubeconform` hook validates templates on commit. Requires `kubeconform` installed locally (`go install github.com/yannh/kubeconform/cmd/kubeconform@latest`).

**CI testing:** GitHub Actions runs lint, kubeconform validation, unit tests, and integration tests in kind clusters. Integration tests are auto-discovered from `charts/stampbot/ci/*-values.yaml` files.

**Adding a new integration test case:**
1. Create `charts/stampbot/ci/<name>-values.yaml`
2. Set required values (see `charts/stampbot/ci/README.md`)
3. CI will automatically discover and run the new test case

**Chart versioning:** The chart uses placeholder versions (`0.0.0-placeholder`) in source control. The release workflow automatically replaces these with the actual version when publishing. Never commit a real version number to Chart.yaml.

**Values schema:** Use `@schema` comment blocks in values.yaml to document value types. Example:
```yaml
# @schema
# type: integer
# minimum: 1
# @schema
replicaCount: 2
```

### Adding Dependencies

1. Add to `requirements.txt` (and optionally `pyproject.toml`)
2. Run `make install-dev` (uses venv automatically)
3. Update Dockerfile if needed
4. Test build: `make docker-build`

### Version Pinning Conventions

All dependencies must be pinned to exact versions for reproducibility and security. Renovate manages updates automatically.

**GitHub Actions:** Pin to full SHA with version comment (Renovate-compatible format):
```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
```
- Always use the 40-character commit SHA, never just a tag
- Add a `# vX.Y.Z` comment with the full semantic version for readability
- Renovate will update both the SHA and the version comment automatically

**Docker images:** Pin to digest with tag comment:
```dockerfile
FROM python:3.11-slim@sha256:5be45dbade29bebd6886af6b438fd7e0b4eb7b611f39ba62b430263f82de36d2
```
- Use `image:tag@sha256:digest` format
- Renovate will update digests automatically

**Python packages:** Pin to exact versions:
```
fastapi==0.128.0
```

**Pre-commit hooks:** Pin to version tags (Renovate manages these):
```yaml
- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: v6.0.0
```

**Secret Detection Baseline:** When adding intentional test secrets (e.g., fake keys for CI):
```bash
make secrets-baseline  # Update .secrets.baseline
git add .secrets.baseline
```

## Important Files to Review

When making changes, consider the impact on:

1. **Configuration**: `stampbot/config.py`, `charts/stampbot/values.yaml`
2. **API Surface**: `stampbot/main.py`, `stampbot/webhook_handler.py`
3. **GitHub Integration**: `stampbot/github_client.py`
4. **Deployment**: `Dockerfile`, `charts/stampbot/templates/deployment.yaml`
5. **CI/CD**: `.github/workflows/`

## Debugging

### Enable Debug Logging

```bash
export STAMPBOT_LOG_LEVEL=DEBUG
```

### Test Webhooks Locally

```bash
ngrok http 8000
# Update GitHub App webhook URL
```

### Inspect Metrics

```bash
curl http://localhost:8000/metrics
```

### Check Health

```bash
curl http://localhost:8000/health
```

## Common Issues

### Authentication Errors

- Verify GitHub App ID and private key
- Check private key format (PEM)
- Ensure app is installed on target repo

### Webhook Not Received

- Verify webhook secret matches
- Check ingress/load balancer configuration
- Review GitHub App webhook delivery

### Rate Limiting

- Monitor `github_api_rate_limit_remaining` metric
- Consider implementing caching
- Use conditional requests where possible

## CI/CD Pipeline

### On Pull Request

1. Lint code (ruff format, ruff check, mypy)
2. Run tests with coverage
3. Build Docker image (PR-tagged)
4. Lint Helm chart

### On Merge to Main

1. Run full CI
2. Build multi-arch Docker image
3. Push to container registry

### On Tag (App Release - `v*.*.*`)

1. Build multi-arch Docker image
2. Push to `ghcr.io/dannysauer/stampbot`
3. Create GitHub release with changelog
4. Trigger chart release workflow

### On Chart Changes or After App Release (`chart-v*.*.*`)

1. Calculate next chart version from conventional commits
2. Replace `0.0.0-placeholder` in Chart.yaml with actual version
3. Package Helm chart
4. Push to OCI registry: `oci://ghcr.io/dannysauer/charts/stampbot`
5. Create GitHub release for chart

### Local CI Testing with act

You can run GitHub Actions workflows locally using [nektos/act](https://github.com/nektos/act):

```bash
# Prerequisites: Docker and act installed
# Install act: brew install act (macOS) or see https://github.com/nektos/act

# Run individual jobs
make act-lint       # Run lint job
make act-test       # Run test job
make act-helm       # Run helm-lint job

# Run multiple jobs
make act-ci         # Run lint + test + helm-lint

# Run with custom event
act -j lint --eventpath .github/act/pull_request.json
```

**Note:** Docker build/push jobs require registry authentication and are skipped locally.

## Pre-commit Hooks

Pre-commit hooks run automatically before each commit to catch issues early:

```bash
# One-time setup
make pre-commit-install

# What runs on each commit:
# - Trailing whitespace removal
# - End-of-file fixer
# - YAML/TOML/JSON validation
# - Large file check
# - Private key detection
# - Python AST validation
# - Case conflict detection
# - Branch protection (blocks commits to main/develop)
# - Conventional commit validation (commit-msg stage)
# - Ruff format check
# - Ruff linter
# - MyPy type checking
# - Renovate config validation
# - Helm chart linting

# Run manually on all files
make pre-commit
```

Configuration: `.pre-commit-config.yaml`, `.commitlintrc.yaml`
