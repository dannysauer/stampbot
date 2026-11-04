# Service settings reference

Service settings control the Stampbot process: where it listens, which GitHub
App credentials it uses, and what telemetry it emits. Operators set them through
environment variables or settings files.

Approval rules for a repository live in its `stampbot.toml`, which the
[repository policy reference](repository-policy.md) describes. Repository
policy and service settings don't share a file format or a trust boundary.

## Service setting precedence

Dynaconf loads settings in this order, from lowest to highest precedence:

1. `settings.toml`
2. `.secrets.toml`
3. `.env`
4. Process environment variables

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
| `STAMPBOT_REPO_CONFIG_CACHE_SECONDS` | `repo_config_cache_seconds` | integer, minimum 0 | `300` | No | Seconds a repository's parsed policy stays in memory per replica. A policy edit takes effect after the entry expires. `0` reads policy on every event. A negative, boolean, or non-integer value stops startup with a `ValueError`. The value is read once at startup. Errors are never cached. |
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

The `[defaults]` table in `settings.toml` supplies repository policy when
neither the repository nor its organization has a `stampbot.toml`. It also
fills in any key that a policy file leaves out. The keys are the ones in the
[repository policy reference](repository-policy.md#policy-keys):

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
allowed_authors = []
```

Dynaconf's double-underscore form addresses nested defaults. For example,
`STAMPBOT_DEFAULTS__CHATOPS_REQUIRED_PERMISSION=write` changes the service-wide
ChatOps threshold.

Stampbot validates defaults each time it loads policy for a repository that
uses them. An invalid default stops automation for that repository, the same
as an invalid policy file. Repository users see only that the defaults are
invalid and that they should contact you; Stampbot logs the full error, with
the setting and value, as `Invalid service default configuration`. A team in
`allowed_authors` must belong to the organization that owns the event's
repository, so a default that names a team is usually a mistake: it fails for
every repository outside that organization, and it's ignored in repositories
owned by personal accounts. Put team rules in each organization's `.github`
policy instead.

The deprecated `allowed_users` and `allowed_teams` keys still work here and log
a warning when Stampbot uses them.

## GitHub App permissions

| Permission | Level | Used for | Failure behavior |
| --- | --- | --- | --- |
| Pull requests | Read and write | Read state, create reviews, and dismiss Stampbot reviews. | Review lookup, approval, or dismissal fails. |
| Contents | Read-only | Read repository and organization policy. | A missing file advances lookup. An unavailable optional `OWNER/.github` repository advances to service defaults. Other read failures stop automation for the event. |
| Metadata | Read-only | Read required repository metadata. | The installation can't operate normally. |
| Issues | Read-only | Receive and inspect pull request issue comments. | Issue-comment ChatOps is unavailable. |
| Members | Read-only | Check organization team membership. | GitHub can't find listed teams, so Stampbot doesn't approve and comments on the pull request. |
| Administration | Read-only | Read collaborator permission for ChatOps. | Approve and unapprove commands fail closed. |

The App manifest subscribes to these events:

| Event | Actions used |
| --- | --- |
| `pull_request` | `opened`, `reopened`, `labeled`, `unlabeled`, and `synchronize` |
| `issue_comment` | Comments whose issue is a pull request |
| `pull_request_review_comment` | Review comments on a pull request |

GitHub may also send `ping` when testing a webhook. Stampbot answers it even
though it isn't an explicit manifest subscription.
