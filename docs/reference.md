# Stampbot Interface Reference

This document describes Stampbot's external inputs and outputs. Stampbot is a GitHub App
that receives GitHub webhook events and produces pull request reviews, issue comments,
HTTP responses, logs, traces, and Prometheus metrics.

## HTTP Interface

Stampbot listens on port `8000` by default.

| Endpoint | Method | Input | Output |
| --- | --- | --- | --- |
| `/` | `GET` | No body. | Redirects to `/setup` when setup mode is enabled and credentials are missing; otherwise returns service information. |
| `/setup` | `GET` | No body. | HTML setup wizard for creating a GitHub App when setup mode is enabled. |
| `/setup/callback` | `GET` | GitHub manifest callback parameters. | Displays generated GitHub App credentials for local storage. |
| `/setup/status` | `GET` | No body. | JSON setup status and configured App ID when configured. |
| `/webhook` | `POST` | GitHub webhook JSON body with `X-GitHub-Event` and `X-Hub-Signature-256` headers. | JSON status response; may create or dismiss a pull request approval. |
| `/health` | `GET` | No body. | JSON health status. |
| `/metrics` | `GET` | No body. | Prometheus text exposition format. |

The setup wizard posts the generated GitHub App manifest to GitHub's manifest creation
endpoint; that POST is handled by GitHub, not by Stampbot.

## GitHub Webhook Inputs

Stampbot handles these GitHub webhook event families:

- `ping`
- `pull_request`
- `issue_comment`

For `pull_request` events, Stampbot uses the pull request number, title, author, labels,
head SHA, action, repository owner, repository name, and installation ID.

For `issue_comment` events, Stampbot uses the comment body, comment author, issue or pull
request number, repository owner, repository name, and installation ID.

Webhook payloads must pass HMAC-SHA256 signature verification using the configured webhook
secret.

## Pull Request Label Inputs

When `auto_approve_on_label` is enabled, adding one of the configured approval labels can
make Stampbot approve the pull request.

Default approval labels:

```toml
approval_labels = ["autoapprove", "stamp"]
```

Removing an approval label dismisses Stampbot's approval when the pull request no longer
has any configured approval label.

Optional eligibility filters can restrict label-based approval by required labels, title
patterns, allowed users, or allowed teams.

## ChatOps Inputs

When `chatops_enabled` is enabled, authorized users can comment on a pull request with:

| Command | Result |
| --- | --- |
| `@stampbot approve` | Approves the pull request. |
| `@stampbot stamp` | Approves the pull request. |
| `@stampbot unapprove` | Dismisses Stampbot's approval. |
| `@stampbot unstamp` | Dismisses Stampbot's approval. |
| `@stampbot help` | Posts help text for the repository's Stampbot configuration. |

Approval and unapproval commands require at least `chatops_required_permission`, which
defaults to `maintain`.

## Repository Configuration Input

Stampbot reads `stampbot.toml` from the target repository's default branch. If no
repository-level config exists and the repository belongs to an organization, Stampbot also
checks the organization's `.github` repository.

Supported repository configuration keys:

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `approval_labels` | list of strings | `["autoapprove", "stamp"]` | Labels that authorize approval. |
| `auto_approve_on_label` | boolean | `true` | Approve when an approval label is added. |
| `reapprove` | boolean | `false` | Reapprove after new commits when an approval label still applies. |
| `chatops_enabled` | boolean | `true` | Enable issue-comment commands. |
| `chatops_required_permission` | string | `maintain` | Minimum permission for approval and unapproval commands. |
| `approve_commands` | list of strings | `["approve", "stamp"]` | ChatOps command words that approve. |
| `unapprove_commands` | list of strings | `["unapprove", "unstamp"]` | ChatOps command words that dismiss approval. |
| `required_labels` | list of strings | `[]` | PR must have at least one listed label. |
| `required_title_patterns` | list of regex strings | `[]` | PR title must match at least one pattern. |
| `allowed_users` | list of GitHub logins | `[]` | PR author must be listed. |
| `allowed_teams` | list of team slugs | `[]` | PR author must belong to at least one listed team. |

See [stampbot.toml.example](../stampbot.toml.example) for a complete example.

## Application Configuration Input

Stampbot reads application configuration from environment variables, `.secrets.toml`,
`settings.toml`, and `.env`, with environment variables having highest precedence.

Common environment variables:

| Variable | Required | Description |
| --- | --- | --- |
| `STAMPBOT_APP_ID` | Required outside setup mode | GitHub App ID. |
| `STAMPBOT_PRIVATE_KEY` | Required outside setup mode | GitHub App private key value or private key path. |
| `STAMPBOT_WEBHOOK_SECRET` | Required outside setup mode | Shared secret for webhook signature verification. |
| `STAMPBOT_SETUP_ENABLED` | No | Enables the setup wizard. |
| `STAMPBOT_LOG_LEVEL` | No | Logging level. |
| `STAMPBOT_OTEL_ENABLED` | No | Enables OpenTelemetry tracing. |
| `STAMPBOT_OTEL_ENDPOINT` | No | OTLP endpoint. |

## GitHub Outputs

Stampbot can create these externally visible GitHub outputs:

- pull request approval reviews
- dismissed pull request reviews
- help comments in response to `@stampbot help`

Stampbot does not merge pull requests.

## Metrics Output

Stampbot exposes Prometheus metrics from `/metrics`.

| Metric | Description |
| --- | --- |
| `stampbot_http_requests_total` | HTTP requests received. |
| `stampbot_webhook_events_total` | Webhook events received. |
| `stampbot_pr_approvals_total` | Pull request approvals by trigger type. |
| `stampbot_errors_total` | Errors by type. |
| `stampbot_github_api_requests_total` | GitHub API requests. |
| `stampbot_github_api_rate_limit_remaining` | Remaining GitHub API rate limit. |

## Deployment Interfaces

Stampbot is distributed as:

- source code from the GitHub repository
- container images from GitHub Container Registry
- a Helm chart published as an OCI artifact

See [INSTALLATION.md](../INSTALLATION.md) for setup, container, and Kubernetes deployment
instructions.
