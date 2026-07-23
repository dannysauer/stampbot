# Configuration reference

Stampbot reads service settings for the running process and repository policy
for approval decisions. The two layers don't share a file format or trust
boundary.

## Service setting precedence

Dynaconf loads settings in this order, from lowest to highest precedence:

1. `settings.toml`;
2. `.secrets.toml`;
3. `.env`; and
4. process environment variables.

Environment variables use the `STAMPBOT_` prefix. Keep `.env` and
`.secrets.toml` for local work; use the platform's secret store in production.

## Service settings

| Environment variable | File key | Type | Default | Required | Meaning |
| --- | --- | --- | --- | --- | --- |
| `STAMPBOT_APP_NAME` | `app_name` | string | `stampbot` | No | Name written in startup logs. |
| `STAMPBOT_HOST` | `host` | string | `0.0.0.0` | No | Bind address used by `python -m stampbot`. |
| `STAMPBOT_PORT` | `port` | integer | `8000` | No | HTTP port used by `python -m stampbot`. |
| `STAMPBOT_LOG_LEVEL` | `log_level` | string | `INFO` | No | Python and Uvicorn log threshold. |
| `STAMPBOT_LOG_FORMAT` | `log_format` | enum | `auto` | No | `json`, `console`, or `auto`. Auto chooses JSON in Kubernetes and console elsewhere. An unknown value chooses JSON. |
| `STAMPBOT_CLIENT_IP_HEADER` | `client_ip_header` | string | `X-Forwarded-For` | No | Header used for the request log's `client_ip`. An empty string uses the direct peer address. Trust this value only when a controlled proxy overwrites the header. |
| `STAMPBOT_OTEL_ENABLED` | `otel_enabled` | boolean | `false` | No | Enables FastAPI and logging instrumentation. |
| `STAMPBOT_OTEL_ENDPOINT` | `otel_endpoint` | string | unset | With tracing | OTLP gRPC endpoint. Without it, Stampbot logs a warning and exports no spans. |
| `STAMPBOT_OTEL_SERVICE_NAME` | `otel_service_name` | string | `stampbot` | No | OpenTelemetry service name. |
| `STAMPBOT_OTEL_INSECURE` | `otel_insecure` | boolean | `false` | No | Permits plaintext OTLP gRPC for a non-HTTPS endpoint when `true`. An HTTPS endpoint always uses TLS. |
| `STAMPBOT_SETUP_ENABLED` | `setup_enabled` | boolean | `false` | No | Opens setup on an unconfigured instance. It closes after credentials appear. |
| `STAMPBOT_SETUP_ALLOW_CONFIGURED` | `setup_allow_configured` | boolean | `false` | No | Reopens setup after credentials exist. Use only during deliberate reprovisioning. |
| `STAMPBOT_BASE_URL` | `base_url` | URL | unset | With setup | Trusted public origin for manifest callback and webhook URLs. HTTPS is required except on localhost. Request headers are never a fallback. |
| `STAMPBOT_APP_ID` | `app_id` | integer or string | unset | For webhooks | GitHub App ID. |
| `STAMPBOT_PRIVATE_KEY` | `private_key` | PEM or file path | unset | For webhooks | App private key. Inline values begin with `-----BEGIN`; other values are regular-file paths chosen by the operator. Files are limited to 64 KiB and need a complete private-key PEM envelope. |
| `STAMPBOT_WEBHOOK_SECRET` | `webhook_secret` | string | unset | For webhooks | Secret used to verify `X-Hub-Signature-256`. |
| `STAMPBOT_METRICS_ENABLED` | `metrics_enabled` | boolean | `false` | No | Starts a separate Prometheus listener. The main HTTP listener never serves `/metrics`. |
| `STAMPBOT_METRICS_HOST` | `metrics_host` | nonempty string | `127.0.0.1` | When metrics are enabled | Bind address for the metrics listener. Use a non-loopback address only behind a trusted network boundary. |
| `STAMPBOT_METRICS_PORT` | `metrics_port` | integer, 1–65535 | `9090` | When metrics are enabled | Metrics listener port. It must differ from `STAMPBOT_PORT`. |

The OTLP exporter uses TLS and the system certificate store by default. Set the
standard OpenTelemetry variable `OTEL_EXPORTER_OTLP_CERTIFICATE` to a PEM CA
certificate path when the collector uses a private certificate authority. Set
`STAMPBOT_OTEL_INSECURE=true` only for a collector on an isolated development
network.

Stampbot is configured only when App ID, private key, and webhook secret are all
present. Without all three, `POST /webhook` returns `503`.

### Setup gates

An unconfigured setup flow needs both settings below:

```dotenv
STAMPBOT_SETUP_ENABLED=true
STAMPBOT_BASE_URL=https://stampbot.example.com
```

Setup closes automatically after the three App credentials appear. Reopening
it then requires `STAMPBOT_SETUP_ALLOW_CONFIGURED=true` as a second opt-in.
Configured instances return `403` from setup routes while that override is off.

### Metrics boundary

The metrics listener has no application-level authentication. Leave it disabled
unless a local-only or private monitoring network can reach its address. The
Helm chart handles this by creating a separate ClusterIP Service that the chart
Ingress doesn't reference.

## Service-wide policy defaults

The `[defaults]` table in `settings.toml` supplies repository policy when no
repository or organization file exists:

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

Dynaconf's double-underscore form addresses nested defaults. For example,
`STAMPBOT_DEFAULTS__CHATOPS_REQUIRED_PERMISSION=write` changes the service-wide
ChatOps threshold.

## Repository policy lookup

For each event, Stampbot checks these locations in order:

1. `stampbot.toml` on the target repository's default branch;
2. `stampbot.toml` in `OWNER/.github` when the owner is an organization; and
3. the service-wide defaults.

The first file found wins. Stampbot doesn't merge repository and organization
files.

## Repository policy keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `approval_labels` | list of strings | `["autoapprove", "stamp"]` | Labels that trigger approval. Removing any configured approval label can dismiss active Stampbot approvals. |
| `auto_approve_on_label` | boolean | `true` | Enables label-driven approval and dismissal on label removal. |
| `reapprove` | boolean | `false` | Lets a `synchronize` event approve a new head when an approval label remains. |
| `chatops_enabled` | boolean | `true` | Enables `@stampbot` commands in pull request issue and review comments. |
| `chatops_required_permission` | enum | `maintain` | Minimum permission for approve and unapprove commands: `none`, `read`, `triage`, `write`, `maintain`, or `admin`. |
| `approve_commands` | list of strings | `["approve", "stamp"]` | Words that create approval after `@stampbot`. |
| `unapprove_commands` | list of strings | `["unapprove", "unstamp"]` | Words that dismiss active Stampbot approvals. |
| `required_labels` | list of strings | `[]` | At least one listed label must be present for label approval. |
| `required_title_patterns` | list of regex strings | `[]` | At least one Python-compatible expression must match the title. At most 20 patterns are accepted; each is limited to 256 characters. |
| `allowed_users` | list of GitHub logins | `[]` | Pull request authors allowed by the author filter. |
| `allowed_teams` | list of team slugs | `[]` | Organization teams allowed by the author filter. Values may be `team-slug` or `org/team-slug`. |

An empty filter list disables that filter. `allowed_users` and `allowed_teams`
form one author category, so a direct user match or a team match passes it.

### Title-pattern limits

Title patterns use Python `re`-compatible behavior. Stampbot compiles them when
it loads policy and evaluates them in order. Each pattern receives 10 ms
against a title no longer than 256 characters; the first match passes.

Matching runs in a worker thread. A timeout or engine failure rejects approval
for that event and reports a bounded-evaluation reason. Rewrite an expensive
expression instead of depending on more time.

The policy parser validates TOML syntax, the permission enum, and title-pattern
type, count, length, and syntax. It doesn't reject every unknown key or wrong
TOML type. Use the types in this table.

Command parsing reads one `\w+` word after `@stampbot`. Custom command words may
contain letters, digits, and underscores; spaces and hyphens end the word.

## Filter evaluation

Filter categories combine with AND. Values within one category combine with OR.

```toml
approval_labels = ["autoapprove"]
required_labels = ["safe-to-approve", "dependency-update"]
required_title_patterns = ["^fix:", "^chore\\(deps\\):"]
allowed_users = ["renovate", "dependabot"]
allowed_teams = ["platform-automation"]
```

This policy needs the `autoapprove` label and one required label. The title must
match one expression. The author must match either a listed user or a listed
team.

These filters apply only to label approval. ChatOps uses the commenter's
repository permission.

## Missing and invalid policy

An absent policy file moves lookup to the next source. The `OWNER/.github`
repository is optional: when it doesn't exist or the App installation doesn't
include it, GitHub returns a repository-level `404` and Stampbot uses service
defaults.

A failure reading the target repository's policy stops automation for that
event. Once GitHub makes the organization repository available to the App, a
failure reading its policy does too. A readable but invalid policy also stops
automation. Each failure increments the configuration-load error metric. On a
newly opened pull request, Stampbot leaves a review comment: validation failures
name the invalid setting, while read failures use a generic message. Other pull
request and ChatOps events return an error without changing a review.

A title-pattern timeout is different: the policy parsed successfully, but that
pull request is ineligible. Stampbot creates no approval.

## GitHub App permissions

| Permission | Level | Used for | Failure behavior |
| --- | --- | --- | --- |
| Pull requests | Read and write | Read state, create reviews, and dismiss Stampbot reviews. | Review lookup, approval, or dismissal fails. |
| Contents | Read-only | Read repository and organization policy. | A missing file advances lookup. An unavailable optional `OWNER/.github` repository advances to service defaults. Other read failures stop automation for the event. |
| Metadata | Read-only | Read required repository metadata. | The installation can't operate normally. |
| Issues | Read-only | Receive and inspect pull request issue comments. | Issue-comment ChatOps is unavailable. |
| Members | Read-only | Check organization team membership. | Team filters find no match. |
| Administration | Read-only | Read collaborator permission for ChatOps. | Approve and unapprove commands fail closed. |

The App manifest subscribes to these events:

| Event | Actions used |
| --- | --- |
| `pull_request` | `opened`, `reopened`, `labeled`, `unlabeled`, and `synchronize` |
| `issue_comment` | Comments whose issue is a pull request |
| `pull_request_review_comment` | Review comments on a pull request |

GitHub may also send `ping` when testing a webhook. Stampbot answers it even
though it isn't an explicit manifest subscription.
