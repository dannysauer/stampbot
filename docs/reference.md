# Interface reference

Stampbot receives HTTP requests, reads repository policy through GitHub, and
writes review state through the GitHub App installation API.

## Public HTTP service

The default bind address is `0.0.0.0:8000`.

| Method and path | Success | Other responses |
| --- | --- | --- |
| `GET /` | `200` JSON with `app`, `version`, and `status`. | `307` to `/setup` when setup is available and credentials are absent. |
| `GET /health` | `200 {"status":"healthy"}`. | None defined. |
| `GET /ready` | `200` when configured or setup is available. | `503` when neither condition is true. |
| `POST /webhook` | `200` handler result after authentication and parsing. | `400`, `401`, `413`, `503`, or `500`. |
| `GET /setup` | `200` GitHub App manifest form while setup is available. | `403` when closed; `503` for a missing or invalid base URL. |
| `GET /setup/callback` | `200` credential page for a valid manifest code. | `403`, `422`, or `500`. |
| `GET /setup/status` | `200` with `configured` and `setup_enabled` while setup is available. | `403` when setup is closed. |
| `GET /metrics` | None. | `404`; metrics are never registered on this listener. |
| `GET /openapi.json` | FastAPI OpenAPI JSON. | Framework errors. |
| `GET /docs` | Swagger UI. | Framework errors. |
| `GET /redoc` | ReDoc UI. | Framework errors. |

`/health` is a shallow liveness signal. `/ready` reports whether Stampbot can
serve webhooks or can still serve first-run setup. It does not call GitHub.

## Setup access

Setup is available only when `STAMPBOT_SETUP_ENABLED=true`. It also requires a
valid `STAMPBOT_BASE_URL`. HTTPS is required except for localhost addresses.

Credentials close setup automatically. A configured instance also needs
`STAMPBOT_SETUP_ALLOW_CONFIGURED=true` before any setup route opens.

Manifest URLs come only from `STAMPBOT_BASE_URL`. The service ignores `Host`,
`X-Forwarded-Host`, and `X-Forwarded-Proto` for this purpose.

Setup HTML responses use the following controls:

- `Cache-Control: no-store`;
- a frame prohibition;
- a restrictive referrer policy; and
- HTML escaping for generated values.

`GET /setup/status` never returns the App ID. The callback page displays the new
private key and webhook secret because the manifest flow has to deliver them to
the operator.

## Webhook request

`POST /webhook` requires:

- `X-GitHub-Event` with an event name;
- `X-Hub-Signature-256` with an HMAC-SHA256 signature of the raw body;
- a JSON body no larger than 1 MiB; and
- App ID, private key, and webhook secret at the service.

Stampbot verifies the signature before parsing JSON. It compares signatures in
constant time.

| HTTP status | Cause |
| --- | --- |
| `200` | Authentication and parsing succeeded. The handler result may be `success`, `ignored`, `ok`, or `error`. |
| `400` | The event header is missing or the body is invalid JSON. |
| `401` | The signature is missing or does not match. |
| `413` | The declared or actual body exceeds 1 MiB. |
| `503` | At least one App credential is missing. |
| `500` | The handler raised an unhandled exception. |

A handler-level error remains an HTTP `200` unless the handler raises.

## Webhook events

| Event | Behavior |
| --- | --- |
| `ping` | Returns `{"status":"ok","message":"pong"}`. |
| `pull_request` | Acts on `opened`, `reopened`, `labeled`, `synchronize`, and `unlabeled`. Any other action returns `{"status":"ignored","message":"Action ACTION not handled"}` without reading policy or calling GitHub. |
| `issue_comment` | Handles `@stampbot` commands only when the issue is a pull request. |
| `pull_request_review_comment` | Handles `@stampbot` commands in review comments. |
| Any other event | Returns an ignored result. |

Pull request handling reads the number, title, author, labels, head SHA,
repository identity, default branch, owner type, and installation ID. Missing
required fields produce a handler-level error.

## Repository policy lookup

Stampbot checks policy in this order:

1. `stampbot.toml` on the repository's default branch;
2. `stampbot.toml` in the owner organization's `.github` repository; and
3. service-wide defaults.

The organization fallback applies only to organization-owned repositories. A
missing file continues lookup. The optional organization repository may also be
absent or outside the App installation. GitHub reports both cases as a
repository-level `404`, so that response continues lookup to service defaults.

A failure reading the target repository's policy stops automation for that
event. Once GitHub makes the organization repository available to the App, a
failure reading its policy does too. A readable but invalid file also stops
automation.

Valid results, including a missing file, stay in memory per replica for
`STAMPBOT_REPO_CONFIG_CACHE_SECONDS` (default 300). Invalid policy and read
failures are never cached. `stampbot_repo_config_loads_total{status="cached"}`
counts events served from that cache; a cache hit creates no
`webhook.get_repo_config` span.

See [Configuration reference](configuration.md#repository-policy) for every
field and validation rule.

## Label-driven approval

`auto_approve_on_label` controls the behavior below.

| Pull request action | Conditions | Result |
| --- | --- | --- |
| `opened` with an approval label | The label was present when the pull request was created. | Ignore the event; GitHub sends a `labeled` event for each label present at creation, and that event creates the approval. The response reads `Approval label handled by the labeled event`. If that `labeled` delivery fails, redeliver it; the `opened` event does not approve on redelivery. |
| `reopened` | A current label is configured and every eligibility filter passes. | Create an approval unless an active Stampbot approval exists. |
| `labeled` with an approval label | The new label is the first configured approval label present on the pull request, and every filter passes. | Create an approval unless an active Stampbot approval exists. |
| `labeled` with a second approval label | Another configured approval label that sorts earlier in `approval_labels` is already present. | Ignore the event; the event for the earlier label creates the approval. |
| `labeled` with another label | An approval label remains, a prior Stampbot review exists, and every filter passes. | Refresh approval when no active review covers the head. |
| `synchronize` | `reapprove=true`, an approval label remains, a prior Stampbot review exists, and every filter passes. | Approve the new head. |
| `unlabeled` | The removed label is in `approval_labels`. | Dismiss active Stampbot approvals. |

Removing one configured approval label dismisses the review even when another
configured approval label remains.

The `opened` event still reads policy: it posts the invalid-policy review
comment and warns about approval labels that do not exist in the repository.

Title filtering accepts at most 20 patterns. Each pattern and the title are
limited to 256 characters. Each pattern has a 10 ms match budget. Matching runs
outside the asyncio event loop and fails closed on timeout or engine error.

## ChatOps

Stampbot lowercases and trims comments before searching for
`@stampbot <command>`. Comments over 65,536 characters are ignored.

| Default command | Permission | Result |
| --- | --- | --- |
| `@stampbot approve` | `maintain`, or the configured threshold | Approve the current head. |
| `@stampbot stamp` | Same as `approve` | Approve the current head. |
| `@stampbot unapprove` | `maintain`, or the configured threshold | Dismiss active Stampbot approvals. |
| `@stampbot unstamp` | Same as `unapprove` | Dismiss active Stampbot approvals. |
| `@stampbot help` | No collaborator check | Post effective commands, labels, permission, and filters. |

Custom command words come from repository policy. A command is one `\w+` word.
An unknown word returns an ignored result.

ChatOps authorization is separate from label-driven eligibility filters.

## GitHub writes

Stampbot may create:

- an approval review;
- a dismissal of one of its own active approvals;
- a review comment describing invalid policy on a newly opened pull request; or
- an issue comment in response to `@stampbot help`.

Stampbot does not dismiss another identity's review or merge a pull request.

## Metrics service

Metrics are disabled by default. When enabled, the separate listener defaults
to `127.0.0.1:9090`. Its port must be in the range 1–65535 and must differ from
the public HTTP port. The listener has no application authentication.

The Helm chart binds this listener to `0.0.0.0` inside the Pod and creates a
separate ClusterIP Service. Its Ingress and main Service expose only the public
HTTP listener. ServiceMonitor selects only the metrics Service.

| Metric | Type | Labels |
| --- | --- | --- |
| `stampbot_info` | Info | `version` |
| `stampbot_http_requests_total` | Counter | `method`, `endpoint`, `status` |
| `stampbot_http_request_duration_seconds` | Histogram | `method`, `endpoint` |
| `stampbot_http_request_size_bytes` | Histogram | `method`, `endpoint` |
| `stampbot_http_response_size_bytes` | Histogram | `method`, `endpoint` |
| `stampbot_http_requests_in_progress` | Gauge | `method`, `endpoint` |
| `stampbot_webhook_events_total` | Counter | `event_type`, `action` |
| `stampbot_webhook_signature_validations_total` | Counter | `result` |
| `stampbot_webhook_processing_duration_seconds` | Histogram | `event_type` |
| `stampbot_pr_approvals_total` | Counter | `trigger_type`, `status` |
| `stampbot_pr_approval_duration_seconds` | Histogram | none |
| `stampbot_pr_dismissals_total` | Counter | `trigger_type`, `status` |
| `stampbot_pr_dismissal_duration_seconds` | Histogram | none |
| `stampbot_chatops_commands_total` | Counter | `command`, `status` |
| `stampbot_github_api_requests_total` | Counter | `operation`, `status` |
| `stampbot_github_api_request_duration_seconds` | Histogram | `operation` |
| `stampbot_github_api_rate_limit_remaining` | Gauge | `installation_id` |
| `stampbot_github_api_rate_limit_limit` | Gauge | `installation_id` |
| `stampbot_repo_config_loads_total` | Counter | `status` |
| `stampbot_errors_total` | Counter | `error_type` |

HTTP metrics use the matched FastAPI route template as `endpoint`. Unmatched
requests share `unmatched`. A method-not-allowed response uses its matching
route template and status `405`. Raw URL paths do not become labels.

## OpenTelemetry

Tracing is disabled by default. When enabled, the OTLP gRPC exporter uses TLS.
`STAMPBOT_OTEL_INSECURE=true` permits plaintext only for a non-HTTPS endpoint.
An HTTPS endpoint cannot be downgraded.

A webhook trace contains these spans:

| Span | Source | Key attributes |
| --- | --- | --- |
| `POST /webhook` | FastAPI instrumentation | HTTP route, status, and client |
| `webhook.*` | Stampbot | `webhook.event_type`, `webhook.action`, `webhook.result`, `github.repo`, `github.pr_number`, `github.delivery_id`, `config.result` |
| `github.*` | Stampbot | `github.installation_id`, `github.result`, `github.reviews_found` |
| `GET`, `POST`, `PUT` | `requests` instrumentation | `http.url`, `http.status_code` for each GitHub API request |

`github.delivery_id` is the `X-GitHub-Delivery` header, so a delivery in the
GitHub App's **Recent Deliveries** page can be found in Tempo and Loki. The
same value appears in log records as `delivery_id`. Values longer than 64
characters or containing characters other than letters, digits, and hyphens
are dropped. `GET /health` and `GET /ready` are not traced.

`OTEL_EXPORTER_OTLP_CERTIFICATE` selects a PEM CA file for a private certificate
authority. The Helm chart can mount that file from an existing Secret.

## Runtime version

Published images receive the computed release version during the build. One
resolved value appears in:

- the root response;
- OpenAPI metadata;
- `stampbot_info`; and
- the OpenTelemetry `service.version` resource attribute.

A source installation falls back to distribution metadata. An unversioned
source-container build reports `0.0.0+unknown`. `make docker-build` defaults to
`0.0.0+local` unless `APP_VERSION` is set.

## GitHub client behavior

GitHub requests use a 30-second timeout. The client permits up to three retries
with exponential backoff for `500`, `502`, `503`, and `504` responses.

Stampbot keeps installation credentials for at most 256 installations per
replica, each for one hour after creation. The first operation for an
installation exchanges the App JWT for an installation token; later operations
reuse the token, and PyGithub refreshes it shortly before GitHub expires it.
Each operation builds its own client on those credentials, so no connection
state is shared between threads. In steady state
`stampbot_github_api_requests_total{operation="get_token"}` rises about once
per hour per active installation.

Repository and pull request objects are lazy. A GitHub operation requests only
the resources it reads or writes, and the remaining rate limit comes from the
`x-ratelimit-*` headers of those responses instead of a separate request. The
App's bot login is read once per hour. Parsed repository policy is kept for at
most 1,024 repositories per replica.

Logs scrub common GitHub token formats. Operators still need to remove private
repository and customer data before sharing logs.

## Distribution

| Artifact | Location |
| --- | --- |
| Source | <https://github.com/dannysauer/stampbot> |
| Container | `ghcr.io/dannysauer/stampbot` and `docker.io/stampbot/stampbot` |
| Helm chart | `oci://ghcr.io/dannysauer/charts/stampbot` |

Use [Install Stampbot](../INSTALLATION.md) for deployment procedures. Use
[Verify a release](release-verification.md) before promotion.
