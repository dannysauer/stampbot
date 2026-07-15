# Interface reference

Stampbot exposes one HTTP service and writes results through the GitHub API.
This page describes those interfaces as implemented.

## HTTP endpoints

The default bind address is `0.0.0.0:8000`.

| Method and path | Input | Success response | Other responses |
| --- | --- | --- | --- |
| `GET /` | None | `307` to `/setup` when setup is enabled and credentials are missing. Otherwise, JSON with `app`, `version`, and `status`. | None expected. |
| `GET /health` | None | `200 {"status":"healthy"}`. This is liveness only. | None expected. |
| `GET /ready` | None | `200` when credentials are complete or setup mode is enabled. The JSON includes `configured` and `setup_enabled` checks. | `503` when credentials are incomplete and setup is disabled. |
| `GET /metrics` | None | `200` with Prometheus text. | None expected. |
| `POST /webhook` | Signed GitHub webhook JSON | `200` with handler `status` and `message`. A handler-level error may still use HTTP `200`. | `400`, `401`, `413`, `503`, or `500`. |
| `GET /setup` | None | `200` setup page or already-configured page. | `403` when setup is disabled. |
| `GET /setup/callback` | GitHub manifest `code` query value | `200` page containing the new App credentials. | `403`, `422` for a missing code, or `500` when exchange fails. |
| `GET /setup/status` | None | `200` with `configured`, `setup_enabled`, and `app_id` when configured. | None expected. |
| `GET /openapi.json` | None | FastAPI OpenAPI JSON. | Framework errors. |
| `GET /docs` | None | Swagger UI. | Framework errors. |
| `GET /redoc` | None | ReDoc UI. | Framework errors. |

`/health` doesn't inspect credentials or GitHub. `/ready` is the traffic signal:
an unconfigured process stays ready while setup is enabled so the setup page can
still receive traffic.

The current application constant reports version `0.1.0` in the root response,
OpenAPI document, metric, and tracing resource.

## Webhook request

`POST /webhook` requires:

- `X-GitHub-Event` with the event name;
- `X-Hub-Signature-256` with an HMAC-SHA256 signature of the raw body;
- a JSON body no larger than 1 MiB; and
- all three GitHub App credentials on the server.

| HTTP status | Cause |
| --- | --- |
| `200` | The signature and JSON were valid. The handler result may be `success`, `ignored`, `ok`, or `error`. |
| `400` | The event header is missing or the body isn't valid JSON. |
| `401` | The signature is missing or doesn't match. |
| `413` | `Content-Length` or the body itself exceeds 1 MiB. |
| `503` | App ID, private key, or webhook secret is missing. |
| `500` | Event handling raised an unhandled exception. |

## Webhook events

| Event | Fields and behavior |
| --- | --- |
| `ping` | Returns `{"status":"ok","message":"pong"}`. |
| `pull_request` | Reads action, number, title, author, labels, head SHA, repository identity, default branch, owner type, and installation ID. |
| `issue_comment` | Handles comments only when the issue is a pull request. |
| `pull_request_review_comment` | Handles `@stampbot` commands in review comments. |
| Any other event | Returns an `ignored` result. |

Missing pull request, repository, or installation fields produce a handler-level
`error` result. The HTTP response remains `200` unless the handler raises.

## Label-driven approval

`auto_approve_on_label` controls all behavior in this table.

| Pull request action | Condition | Result |
| --- | --- | --- |
| `opened` or `reopened` | Any current label appears in `approval_labels` and every eligibility filter passes. | Create approval unless an active Stampbot approval already exists. |
| `labeled` with an approval label | The added label is configured and every filter passes. | Create approval unless an active approval exists. |
| `labeled` with another label | A configured approval label remains, a previous Stampbot review exists, and no active approval covers the current head. Filters must pass. | Refresh approval. |
| `synchronize` | `reapprove = true`, a configured approval label remains, a previous Stampbot review exists, and no active approval covers the new head. Filters must pass. | Create a fresh approval. |
| `unlabeled` | The removed label appears in `approval_labels`. | Dismiss every active Stampbot approval on the pull request. |

Removing one configured approval label dismisses the review even when another
configured approval label remains. A later matching event can approve again.

## ChatOps

Stampbot lowercases and trims a comment before searching for
`@stampbot <command>`. It ignores comments over 65,536 characters.

| Default command | Permission | Result |
| --- | --- | --- |
| `@stampbot approve` | `maintain` or configured threshold | Approve the current head unless it already has an active Stampbot approval. |
| `@stampbot stamp` | `maintain` or configured threshold | Same as `approve`. |
| `@stampbot unapprove` | `maintain` or configured threshold | Dismiss active Stampbot approvals. |
| `@stampbot unstamp` | `maintain` or configured threshold | Same as `unapprove`. |
| `@stampbot help` | No collaborator check | Post the effective commands, permission threshold, labels, and filters. |

Custom approve and unapprove words come from repository policy. An unknown word
returns an `ignored` result.

## GitHub writes

Stampbot may create:

- an approval review;
- a dismissal of one of its active approval reviews;
- a review comment describing invalid policy on a newly opened pull request; or
- an issue comment in response to `@stampbot help`.

It doesn't dismiss another reviewer's approval and doesn't merge the pull
request.

## Prometheus metrics

`GET /metrics` is always registered on the main service. The current
`metrics_enabled` and `metrics_port` settings don't change it.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `stampbot_info` | Info | `version` | Application build information. |
| `stampbot_http_requests_total` | Counter | `method`, `endpoint`, `status` | HTTP responses. |
| `stampbot_http_request_duration_seconds` | Histogram | `method`, `endpoint` | HTTP duration. |
| `stampbot_http_request_size_bytes` | Histogram | `method`, `endpoint` | Request `Content-Length` when present. |
| `stampbot_http_response_size_bytes` | Histogram | `method`, `endpoint` | Response `Content-Length` when present. |
| `stampbot_http_requests_in_progress` | Gauge | `method`, `endpoint` | Requests currently running. |
| `stampbot_webhook_events_total` | Counter | `event_type`, `action` | Authenticated events routed to the handler. |
| `stampbot_webhook_signature_validations_total` | Counter | `result` | Valid and invalid signature checks. |
| `stampbot_webhook_processing_duration_seconds` | Histogram | `event_type` | Handler duration. |
| `stampbot_pr_approvals_total` | Counter | `trigger_type`, `status` | Approval attempts. |
| `stampbot_pr_approval_duration_seconds` | Histogram | none | Approval operation duration. |
| `stampbot_pr_dismissals_total` | Counter | `trigger_type`, `status` | Dismissal attempts. |
| `stampbot_pr_dismissal_duration_seconds` | Histogram | none | Dismissal operation duration. |
| `stampbot_chatops_commands_total` | Counter | `command`, `status` | Parsed ChatOps outcomes. |
| `stampbot_github_api_requests_total` | Counter | `operation`, `status` | GitHub client operations. |
| `stampbot_github_api_request_duration_seconds` | Histogram | `operation` | GitHub client duration. |
| `stampbot_github_api_rate_limit_remaining` | Gauge | `installation_id` | Remaining core API quota. |
| `stampbot_github_api_rate_limit_limit` | Gauge | `installation_id` | Core API quota ceiling. |
| `stampbot_repo_config_loads_total` | Counter | `status` | Policy loads: `found`, `default`, or `error`. |
| `stampbot_errors_total` | Counter | `error_type` | Application error categories. |

## Client behavior

GitHub requests use a 30-second timeout. The client configures up to three total
retries with exponential backoff for HTTP `500`, `502`, `503`, and `504`.

Errors returned in logs are scrubbed for common GitHub token formats. Operators
must still remove repository, customer, and credential data before sharing logs.

## Distribution

| Artifact | Location |
| --- | --- |
| Source | <https://github.com/dannysauer/stampbot> |
| Container | `ghcr.io/dannysauer/stampbot` and `docker.io/stampbot/stampbot` |
| Helm chart | `oci://ghcr.io/dannysauer/charts/stampbot` |

Use [Install Stampbot](../INSTALLATION.md) for deployment choices and
[Verify a release](release-verification.md) before promoting an artifact.
