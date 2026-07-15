# Configuration reference

Stampbot has two layers of configuration. Service settings control the running
process; repository policy controls approval in one repository.

## Service setting sources

Dynaconf loads `settings.toml` and then `.secrets.toml`. It also loads a local
`.env` file. Values already present in the process environment take precedence,
and all Stampbot environment variables use the `STAMPBOT_` prefix.

Keep `.env` and `.secrets.toml` for local development. Use your platform's
secret store in production.

## Application settings

| Environment variable | File key | Type | Default | Required | Meaning |
| --- | --- | --- | --- | --- | --- |
| `STAMPBOT_APP_NAME` | `app_name` | string | `stampbot` | No | Name used in startup logs. |
| `STAMPBOT_HOST` | `host` | string | `0.0.0.0` | No | Bind address used by `python -m stampbot`. |
| `STAMPBOT_PORT` | `port` | integer | `8000` | No | HTTP port used by `python -m stampbot`. |
| `STAMPBOT_LOG_LEVEL` | `log_level` | string | `INFO` | No | Python and Uvicorn logging threshold. |
| `STAMPBOT_LOG_FORMAT` | `log_format` | enum | `auto` | No | `json`, `console`, or `auto`. Auto selects JSON in Kubernetes. An unknown value also selects JSON. |
| `STAMPBOT_CLIENT_IP_HEADER` | `client_ip_header` | string | `X-Forwarded-For` | No | Header used for the request log's `client_ip`. Set it to an empty string to use the direct peer address. |
| `STAMPBOT_OTEL_ENABLED` | `otel_enabled` | boolean | `false` | No | Enables FastAPI and logging instrumentation. |
| `STAMPBOT_OTEL_ENDPOINT` | `otel_endpoint` | string | unset | When tracing is enabled | OTLP gRPC endpoint. Without it, Stampbot logs a warning and doesn't export spans. |
| `STAMPBOT_OTEL_SERVICE_NAME` | `otel_service_name` | string | `stampbot` | No | OpenTelemetry service name. |
| `STAMPBOT_OTEL_INSECURE` | `otel_insecure` | boolean | `false` | No | Permits plaintext OTLP gRPC for a non-HTTPS endpoint when `true`. An HTTPS endpoint always uses TLS. |
| `STAMPBOT_SETUP_ENABLED` | `setup_enabled` | boolean | `false` | No | Enables setup for an unconfigured instance. Setup closes automatically after credentials are present. |
| `STAMPBOT_SETUP_ALLOW_CONFIGURED` | `setup_allow_configured` | boolean | `false` | No | Reopens setup on a configured instance. Use only for deliberate App reprovisioning, then turn it off again. |
| `STAMPBOT_BASE_URL` | `base_url` | HTTPS URL | unset | When setup is enabled | Trusted public URL used to build manifest callback and webhook URLs. It is never inferred from request headers. Local HTTP is accepted only for `localhost` addresses. |
| `STAMPBOT_APP_ID` | `app_id` | integer or string | unset | Yes for webhooks | GitHub App ID. |
| `STAMPBOT_PRIVATE_KEY` | `private_key` | PEM string or file path | unset | Yes for webhooks | App private key. A value beginning with `-----BEGIN` is read as PEM; any other value is treated as an operator-selected regular-file path. Files are limited to 64 KiB and the value must have a complete private-key PEM envelope. |
| `STAMPBOT_WEBHOOK_SECRET` | `webhook_secret` | string | unset | Yes for webhooks | Secret used to verify `X-Hub-Signature-256`. |
| `STAMPBOT_METRICS_ENABLED` | `metrics_enabled` | boolean | `true` | No | Reserved. The current app serves `/metrics` regardless of this value. |
| `STAMPBOT_METRICS_PORT` | `metrics_port` | integer | `8000` | No | Reserved. Metrics currently use the main HTTP port. |

The OTLP exporter uses TLS and the system certificate store by default. Set the
standard OpenTelemetry variable `OTEL_EXPORTER_OTLP_CERTIFICATE` to a PEM CA
certificate path when the collector uses a private certificate authority. Set
`STAMPBOT_OTEL_INSECURE=true` only for a collector on an isolated development
network.

Stampbot considers itself configured only when App ID, private key, and webhook
secret are all present. Without all three, `POST /webhook` returns `503`.

Setup requires both `STAMPBOT_SETUP_ENABLED=true` and a valid
`STAMPBOT_BASE_URL`. Once all three App credentials are present, setup routes
return `403` even if the first flag was accidentally left on. Reopening those
routes additionally requires `STAMPBOT_SETUP_ALLOW_CONFIGURED=true`. This is a
break-glass reprovisioning control, not a normal production setting.

## Service-wide repository defaults

The `[defaults]` table in `settings.toml` supplies policy when no repository
file exists:

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

Dynaconf environment variables can override nested defaults with its double
underscore form, such as
`STAMPBOT_DEFAULTS__CHATOPS_REQUIRED_PERMISSION=write`.

## Repository policy source

For each event, Stampbot checks:

1. `stampbot.toml` on the target repository's default branch;
2. `stampbot.toml` in `OWNER/.github` when `OWNER` is an organization; and
3. the service-wide defaults above.

The target repository wins. Stampbot doesn't merge a repository file with the
organization file.

## Repository policy keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `approval_labels` | list of strings | `["autoapprove", "stamp"]` | Labels that can trigger approval. Removing any one of them can dismiss active Stampbot approvals. |
| `auto_approve_on_label` | boolean | `true` | Enables label-driven approval and label-removal dismissal. |
| `reapprove` | boolean | `false` | Lets `synchronize` refresh a previous Stampbot review when an approval label remains on the pull request. |
| `chatops_enabled` | boolean | `true` | Enables `@stampbot` commands in pull request comments and review comments. |
| `chatops_required_permission` | enum | `maintain` | Minimum permission for approve and unapprove commands: `none`, `read`, `triage`, `write`, `maintain`, or `admin`. |
| `approve_commands` | list of strings | `["approve", "stamp"]` | Words that create approval after `@stampbot`. |
| `unapprove_commands` | list of strings | `["unapprove", "unstamp"]` | Words that dismiss active Stampbot approvals. |
| `required_labels` | list of strings | `[]` | At least one listed label must be present for label-driven approval. |
| `required_title_patterns` | list of regex strings | `[]` | At least one Python-compatible regular expression must match the pull request title. Maximum 20 patterns, each at most 256 characters. |
| `allowed_users` | list of GitHub logins | `[]` | Directly allowed pull request authors. |
| `allowed_teams` | list of team slugs | `[]` | Organization teams whose members may pass the author filter. Use `team-slug` or `org/team-slug`. |

Empty filter lists disable that filter. `allowed_users` and `allowed_teams` form
one author filter: a direct user match or a team match is enough.

The parser validates TOML syntax, the permission enum, and title-pattern type,
count, length, and syntax. It doesn't currently reject unknown keys or every
wrong TOML type. Use the documented types; stricter validation may arrive
later.

Title patterns use Python `re`-compatible behavior. Stampbot evaluates them in
configuration order with a 10 ms limit for each pattern, against a title of at
most 256 characters. Matching runs in a worker thread so it cannot stall the
asyncio event loop. The first match passes the title filter. If a pattern times
out or the matching engine fails before a match, Stampbot rejects approval for
that event and reports the safety-limit reason. Rewrite an expensive pattern or
split it into simpler alternatives instead of relying on more evaluation time.

Command parsing reads one `\w+` word after `@stampbot`. Use letters, digits, or
underscores in custom commands.

## Filter logic

Filter categories combine with AND. Values inside one category combine with OR.

Suppose a repository uses:

```toml
approval_labels = ["autoapprove"]
required_labels = ["safe-to-approve", "dependency-update"]
required_title_patterns = ["^fix:", "^chore\\(deps\\):"]
allowed_users = ["renovate", "dependabot"]
allowed_teams = ["platform-automation"]
```

The pull request needs `autoapprove` and one required label. Its title must match
one pattern, and its author must match either the user list or the team list.

These filters apply only to label-driven approval. ChatOps approval uses the
commenter's repository permission instead.

## Invalid and unavailable policy

If a policy file is absent, Stampbot follows the source order above.

If a GitHub read fails, Stampbot records a configuration-load error and uses
service defaults. This includes failures that the GitHub client reports as a
missing file.

If Stampbot reads the file but can't parse or validate it, automation stops for
that event. On a newly opened pull request, Stampbot leaves a review comment
with the validation error. Other pull request actions and ChatOps events return
an error result without changing a review. A title-pattern timeout occurs while
evaluating an otherwise valid policy; it makes that pull request ineligible and
doesn't create an approval.

## GitHub App permissions

| Permission | Level | Used for | Failure behavior |
| --- | --- | --- | --- |
| Pull requests | Read and write | Read pull request state, create reviews, and dismiss Stampbot reviews. | Review lookup, approval, or dismissal fails. |
| Contents | Read-only | Read repository and organization policy files. | The client reports no file and Stampbot uses the next fallback. |
| Metadata | Read-only | Read required repository metadata. | The installation can't operate normally. |
| Issues | Read-only | Receive and inspect pull request issue comments. | ChatOps issue comments aren't available. |
| Members | Read-only | Check allowed organization teams. | Team checks return no match. |
| Administration | Read-only | Read collaborator permission for ChatOps. | Approve and unapprove commands fail closed. |

Stampbot's manifest subscribes to:

| Event | Actions Stampbot uses |
| --- | --- |
| `pull_request` | `opened`, `reopened`, `labeled`, `unlabeled`, and `synchronize` |
| `issue_comment` | Comments on pull requests |
| `pull_request_review_comment` | Review comments on pull requests |

GitHub can also send `ping` while testing a webhook. Stampbot answers it even
though it isn't an explicit manifest subscription.
