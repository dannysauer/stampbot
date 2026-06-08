# Stampbot Interface Reference

Stampbot is a GitHub App that receives GitHub webhooks and produces pull request reviews,
issue comments, HTTP responses, structured logs, traces, and Prometheus metrics.

For configuration keys, defaults, permissions, events, and failure behavior, see
[Configuration Reference](configuration.md).

## HTTP Interface

Stampbot listens on `STAMPBOT_PORT` (`8000` by default).

| Endpoint | Method | Input | Success output | Error output |
| --- | --- | --- | --- | --- |
| `/` | `GET` | No body. | Redirects to `/setup` with `307` when setup mode is enabled and credentials are missing; otherwise returns `{"app":"stampbot","version":"0.1.0","status":"running"}`. | None expected. |
| `/health` | `GET` | No body. | `{"status":"healthy"}`. | None expected. |
| `/metrics` | `GET` | No body. | Prometheus text exposition format. | None expected. |
| `/webhook` | `POST` | GitHub webhook JSON body with `X-GitHub-Event` and `X-Hub-Signature-256`. | JSON `status` and `message`; may create or dismiss a pull request approval or post help. | `400`, `401`, `413`, `503`, or `500` with JSON `detail`. |
| `/setup` | `GET` | No body. | HTML setup wizard when setup is enabled. | `403` when setup is disabled. |
| `/setup/callback` | `GET` | GitHub manifest `code` query parameter. | HTML page containing generated GitHub App credentials and next steps. | `403` when setup is disabled; `500` when the manifest code exchange fails. |
| `/setup/status` | `GET` | No body. | JSON setup status: `configured`, `setup_enabled`, and `app_id` when configured. | None expected. |
| `/openapi.json` | `GET` | No body. | FastAPI-generated OpenAPI schema. | Framework-generated errors. |
| `/docs` | `GET` | No body. | FastAPI Swagger UI. | Framework-generated errors. |
| `/redoc` | `GET` | No body. | FastAPI ReDoc UI. | Framework-generated errors. |

The setup wizard submits the generated GitHub App manifest to GitHub's manifest creation
endpoint. That POST is handled by GitHub, not by Stampbot.

## Webhook Request Requirements

`POST /webhook` requires:

- `X-GitHub-Event`: GitHub event name.
- `X-Hub-Signature-256`: HMAC-SHA256 signature using `STAMPBOT_WEBHOOK_SECRET`.
- JSON request body no larger than 1 MiB.
- Configured GitHub App credentials: `STAMPBOT_APP_ID`, `STAMPBOT_PRIVATE_KEY`, and
  `STAMPBOT_WEBHOOK_SECRET`.

Webhook response codes:

| Status | Cause |
| --- | --- |
| `200` | Request was authenticated and handled, including ignored no-op events. |
| `400` | Missing event header or invalid JSON body. |
| `401` | Missing or invalid webhook signature. |
| `413` | Request body larger than 1 MiB. |
| `503` | Stampbot is missing required GitHub App credentials. |
| `500` | Internal webhook handling error. |

## GitHub Webhook Inputs

Stampbot handles these event families:

| Event | Actions and payload fields used |
| --- | --- |
| `ping` | Returns `{"status":"ok","message":"pong"}`. |
| `pull_request` | Uses action, PR number, title, author, labels, head SHA, repository owner/name/default branch, owner type, and installation ID. |
| `issue_comment` | Uses PR comments containing `@stampbot`; reads comment body, commenter, issue/PR number, repository owner/name/default branch, owner type, and installation ID. Non-PR issue comments are ignored. |
| `pull_request_review_comment` | Uses review comments containing `@stampbot`; reads comment body, commenter, PR number, repository owner/name/default branch, owner type, and installation ID. |

Subscribed event and permission requirements are listed in
[configuration.md](configuration.md#github-app-permissions).

## Pull Request Label Inputs

When `auto_approve_on_label` is enabled, Stampbot can approve a PR on these
`pull_request` actions:

- `opened`
- `reopened`
- `labeled`
- `synchronize`, only when `reapprove = true` and a previous Stampbot approval is stale

Removing an approval label on an `unlabeled` action dismisses Stampbot approvals when the
removed label is one of the configured approval labels.

Default approval labels:

```toml
approval_labels = ["autoapprove", "stamp"]
```

Eligibility filters are documented in [configuration.md](configuration.md#filter-logic).

## ChatOps Inputs

When `chatops_enabled` is enabled, comments on PRs can use:

| Command | Result |
| --- | --- |
| `@stampbot approve` | Approves the pull request. |
| `@stampbot stamp` | Approves the pull request. |
| `@stampbot unapprove` | Dismisses Stampbot approvals. |
| `@stampbot unstamp` | Dismisses Stampbot approvals. |
| `@stampbot help` | Posts help for the effective repository configuration. |

Approval and unapproval commands require `chatops_required_permission` or higher. The
`help` command does not require a repository permission check. Comments longer than
64 KiB are ignored.

## GitHub Outputs

Stampbot can create these externally visible GitHub outputs:

- Pull request approval reviews.
- Pull request review dismissals for Stampbot's own approvals.
- Pull request review comments describing invalid `stampbot.toml` on newly opened PRs.
- Issue comments in response to `@stampbot help`.

Stampbot does not merge pull requests.

## Metrics Output

Stampbot exposes Prometheus metrics from `/metrics`.

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `stampbot_info` | Info | `version` | Application version. |
| `stampbot_http_requests_total` | Counter | `method`, `endpoint`, `status` | HTTP requests received. |
| `stampbot_http_request_duration_seconds` | Histogram | `method`, `endpoint` | HTTP request duration. |
| `stampbot_http_request_size_bytes` | Histogram | `method`, `endpoint` | HTTP request body size from `Content-Length`. |
| `stampbot_http_response_size_bytes` | Histogram | `method`, `endpoint` | HTTP response body size from `Content-Length`. |
| `stampbot_http_requests_in_progress` | Gauge | `method`, `endpoint` | In-flight HTTP requests. |
| `stampbot_webhook_events_total` | Counter | `event_type`, `action` | Webhook events by event and action. |
| `stampbot_webhook_signature_validations_total` | Counter | `result` | Webhook signature validations, `valid` or `invalid`. |
| `stampbot_webhook_processing_duration_seconds` | Histogram | `event_type` | Webhook handling duration. |
| `stampbot_pr_approvals_total` | Counter | `trigger_type`, `status` | Approval attempts by label or ChatOps trigger. |
| `stampbot_pr_approval_duration_seconds` | Histogram | none | Approval operation duration. |
| `stampbot_pr_dismissals_total` | Counter | `trigger_type`, `status` | Dismissal attempts by label removal or ChatOps trigger. |
| `stampbot_pr_dismissal_duration_seconds` | Histogram | none | Dismissal operation duration. |
| `stampbot_chatops_commands_total` | Counter | `command`, `status` | ChatOps commands by parsed command and outcome. |
| `stampbot_github_api_requests_total` | Counter | `operation`, `status` | GitHub API calls. |
| `stampbot_github_api_request_duration_seconds` | Histogram | `operation` | GitHub API call duration. |
| `stampbot_github_api_rate_limit_remaining` | Gauge | `installation_id` | Remaining GitHub API rate limit. |
| `stampbot_github_api_rate_limit_limit` | Gauge | `installation_id` | GitHub API rate limit ceiling. |
| `stampbot_repo_config_loads_total` | Counter | `status` | Repository config loads: `found`, `default`, or `error`. |
| `stampbot_errors_total` | Counter | `error_type` | Application error categories. |

## Distribution Interfaces

Stampbot is distributed as:

- Source code from this GitHub repository.
- Container images from GitHub Container Registry and Docker Hub release workflows.
- A Helm chart published as an OCI artifact under `ghcr.io/dannysauer/charts/stampbot`.

See [INSTALLATION.md](../INSTALLATION.md), [deploy-gcp-cloudrun.md](deploy-gcp-cloudrun.md),
and [charts/stampbot/README.md](../charts/stampbot/README.md) for deployment procedures.
