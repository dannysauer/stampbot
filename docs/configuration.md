# Configuration Reference

Stampbot has two configuration layers:

- Application configuration controls the running service.
- Repository configuration controls approval policy for each target repository.

Application configuration is read by Dynaconf from environment variables, `.secrets.toml`,
`settings.toml`, and `.env`. Environment variables use the `STAMPBOT_` prefix and have the
highest precedence. Use `.env` only for local development.

Repository configuration is loaded from `stampbot.toml` on the target repository's
default branch. If that file is missing and the owner is an organization, Stampbot checks
`<org>/.github` for `stampbot.toml` before falling back to defaults.

## Application Settings

| Environment variable | File key | Type | Default | Required | Runtime behavior |
| --- | --- | --- | --- | --- | --- |
| `STAMPBOT_APP_NAME` | `app_name` | string | `stampbot` | No | Used in startup logging. |
| `STAMPBOT_HOST` | `host` | string | `0.0.0.0` | No | Host used by `python -m stampbot`. |
| `STAMPBOT_PORT` | `port` | integer | `8000` | No | Port used by `python -m stampbot`. |
| `STAMPBOT_LOG_LEVEL` | `log_level` | string | `INFO` | No | Logging threshold passed to Python logging and Uvicorn. |
| `STAMPBOT_LOG_FORMAT` | `log_format` | `json`, `console`, or `auto` | `auto` | No | `auto` uses JSON in Kubernetes and console logs elsewhere. Unknown values fall back to JSON. |
| `STAMPBOT_CLIENT_IP_HEADER` | `client_ip_header` | string | `X-Forwarded-For` | No | Header used for request log `client_ip`; set to an empty string to use the direct connection address. |
| `STAMPBOT_OTEL_ENABLED` | `otel_enabled` | boolean | `false` | No | Enables OpenTelemetry tracing and logging instrumentation. |
| `STAMPBOT_OTEL_ENDPOINT` | `otel_endpoint` | string | unset | Required only when OTel is enabled | OTLP gRPC endpoint. If OTel is enabled without an endpoint, Stampbot logs a warning and continues without tracing export. |
| `STAMPBOT_OTEL_SERVICE_NAME` | `otel_service_name` | string | `stampbot` | No | OpenTelemetry service name. |
| `STAMPBOT_SETUP_ENABLED` | `setup_enabled` | boolean | `true` in source settings, `false` in the Helm chart | No | Enables `/setup` and `/setup/callback`. Disable after initial setup in production. |
| `STAMPBOT_BASE_URL` | `base_url` | URL string | unset | Recommended behind proxies | Overrides setup callback and webhook URL detection. Must be the public base URL, without a trailing path. |
| `STAMPBOT_APP_ID` | `app_id` | integer or string | unset | Yes outside setup mode | GitHub App ID. Missing credentials make `/webhook` return `503`. |
| `STAMPBOT_PRIVATE_KEY` | `private_key` | PEM string or file path | unset | Yes outside setup mode | GitHub App private key. Values beginning with `-----BEGIN` are treated as PEM content; other values are resolved as file paths. Invalid paths or non-PEM content fail GitHub API initialization. |
| `STAMPBOT_WEBHOOK_SECRET` | `webhook_secret` | string | unset | Yes outside setup mode | Shared secret used to verify `X-Hub-Signature-256`. Missing or mismatched secrets make webhook requests fail. |

`settings.toml` also defines `metrics_enabled` and `metrics_port`, but the current
application serves `/metrics` on the main HTTP port regardless of those values.

## Repository Defaults

Repository defaults can be overridden in the `[defaults]` section of `settings.toml` or
with equivalent `STAMPBOT_DEFAULTS__...` Dynaconf environment variables. They become the
fallback policy when a target repository does not provide `stampbot.toml`.

```toml
[defaults]
approval_labels = ["autoapprove", "stamp"]
auto_approve_on_label = true
reapprove = false
chatops_enabled = true
chatops_required_permission = "maintain"
approve_commands = ["approve", "stamp"]
unapprove_commands = ["unapprove", "unstamp"]
required_labels = []
required_title_patterns = []
allowed_users = []
allowed_teams = []
```

## Repository Configuration Keys

| Key | Type | Default | Description | Failure behavior |
| --- | --- | --- | --- | --- |
| `approval_labels` | list of strings | `["autoapprove", "stamp"]` | Labels that can trigger approval and whose removal can dismiss Stampbot approval. | Non-list values are not explicitly type-checked; use the documented type. |
| `auto_approve_on_label` | boolean | `true` | Enables approval on `opened`, `reopened`, `labeled`, and eligible `synchronize` events. | False disables label-triggered approval and label-removal dismissal. |
| `reapprove` | boolean | `false` | Reapproves after new commits when an approval label still applies and no active approval is on the current head. | False ignores `synchronize` events for reapproval. |
| `chatops_enabled` | boolean | `true` | Enables `@stampbot` commands on PR comments and review comments. | False ignores ChatOps comments. |
| `chatops_required_permission` | enum | `maintain` | Minimum repo permission for approval and unapproval commands. Values: `none`, `read`, `triage`, `write`, `maintain`, `admin`. | Invalid values mark config invalid and disable automation for the event. |
| `approve_commands` | list of strings | `["approve", "stamp"]` | Command words after `@stampbot` that create approval. | Unknown commands are ignored with an `Unknown command` response. |
| `unapprove_commands` | list of strings | `["unapprove", "unstamp"]` | Command words after `@stampbot` that dismiss Stampbot approvals. | Unknown commands are ignored with an `Unknown command` response. |
| `required_labels` | list of strings | `[]` | PR must have at least one of these labels in addition to an approval label. | Empty list disables this filter. |
| `required_title_patterns` | list of regex strings | `[]` | PR title must match at least one regular expression. | Invalid regex marks config invalid and disables automation for the event. |
| `allowed_users` | list of GitHub logins | `[]` | PR author must match one listed login when user or team filters are configured. | Empty list disables user filtering unless `allowed_teams` is set. |
| `allowed_teams` | list of team slugs | `[]` | PR author must belong to at least one allowed team. Supports `org/team-slug` and `team-slug`. | Team checks require organization-owned repositories and the GitHub App `members: read` permission. Missing teams or inaccessible membership return no match. |

Unknown keys in `stampbot.toml` are ignored by the current parser. Keep repository files
limited to the documented keys so future validation can become stricter without breaking
your policy.

## Filter Logic

Filter categories use AND logic. If you configure more than one category, every category
must pass.

Within a category, any match is enough:

- Any `required_labels` entry can match.
- Any `required_title_patterns` regex can match.
- Any `allowed_users` login or `allowed_teams` membership can match.

Example:

```toml
approval_labels = ["autoapprove"]
required_labels = ["safe-to-approve", "dependency-update"]
required_title_patterns = ["^fix:", "^chore\\(deps\\):"]
allowed_users = ["renovate", "dependabot"]
allowed_teams = ["platform-automation"]
```

With this policy, the PR needs the `autoapprove` label, one required label, one matching
title pattern, and an author that is either listed directly or belongs to one allowed team.

## ChatOps Commands

Stampbot lowercases and trims comment bodies before parsing commands. A comment must
contain `@stampbot` and a single word command.

| Command class | Default commands | Permission check |
| --- | --- | --- |
| Help | `@stampbot help` | No repository permission check. |
| Approve | `@stampbot approve`, `@stampbot stamp` | Requires `chatops_required_permission` or higher. |
| Unapprove | `@stampbot unapprove`, `@stampbot unstamp` | Requires `chatops_required_permission` or higher. |

ChatOps comments longer than 64 KiB are ignored.

## Invalid Configuration Behavior

If `stampbot.toml` is missing, Stampbot uses repository defaults.

If loading the file fails because GitHub returns an error, Stampbot logs the load failure
and uses defaults.

If TOML parsing, `chatops_required_permission`, or regex validation fails, Stampbot treats
the repository configuration as invalid for the event:

- For `pull_request` events with action `opened`, Stampbot posts a PR review comment that
  includes the validation error and asks maintainers to fix `stampbot.toml`.
- For other pull request actions and ChatOps events, Stampbot returns an error response
  from the webhook handler and takes no GitHub approval action.

## GitHub App Permissions

The GitHub App must have these permissions:

| Permission | Level | Required for | Failure mode |
| --- | --- | --- | --- |
| Pull requests | Read and write | Create approval reviews, dismiss Stampbot reviews, read PR state. | Approval, dismissal, and review lookup fail. |
| Contents | Read-only | Read `stampbot.toml` from repositories and org `.github` fallback. | Repo policy cannot load; defaults are used when reads fail. |
| Metadata | Read-only | Required by GitHub Apps for repository metadata. | Installation cannot operate normally. |
| Issues | Read-only | Receive and inspect PR issue comments for ChatOps. | ChatOps comments cannot be processed reliably. |
| Members | Read-only | Check `allowed_teams` membership. | Team filters fail closed because no allowed team membership is found. |
| Administration | Read-only | Check collaborator permission for ChatOps authorization. | Approval and unapproval commands fail permission checks. |

Subscribed events:

| Event | Actions used |
| --- | --- |
| `pull_request` | `opened`, `reopened`, `labeled`, `unlabeled`, `synchronize` |
| `issue_comment` | PR comments containing `@stampbot` |
| `pull_request_review_comment` | Review comments containing `@stampbot` |

The handler also responds to GitHub `ping` events when they arrive, although `ping` is not
listed in the manifest subscription list.
