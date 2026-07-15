# Architecture

Stampbot sits between GitHub's webhook service and pull request review API. It
keeps no database and owns no merge state.

Think of it as a narrowly instructed reviewer. It may raise its hand, withdraw
that hand, and explain a configuration error. The Merge button remains across
the table. That's as far as the metaphor goes.

## Request path

```mermaid
flowchart LR
    github["GitHub webhook"]
    http["POST /webhook"]
    signature["Verify HMAC"]
    route["Route event"]
    policy["Load policy"]
    decision{"Action allowed?"}
    installation["Create installation client"]
    write["Write review or comment"]
    signals["Logs, metrics, traces"]

    github --> http
    http --> signature
    signature --> route
    route --> policy
    policy --> decision
    decision -->|yes| installation
    installation --> write
    decision -->|no| signals
    route --> signals
    write --> signals
```

GitHub signs the raw request body with the App's webhook secret. The HTTP layer
checks that signature before parsing JSON or routing the event. It also rejects
bodies larger than 1 MiB.

A valid request moves to `WebhookHandler`. The handler loads policy for the
target repository and decides whether the event calls for approval, dismissal,
a help comment, or no action.

When a write is needed, `GitHubAppClient` signs an App JWT and exchanges it for
an installation token. GitHub scopes that token to the installation. The
visible result lands on the pull request timeline; the webhook response only
describes what Stampbot did.

## Review state

Stampbot finds its state in GitHub reviews instead of local storage. Replicas
therefore agree without coordination, and GitHub remains authoritative.

```mermaid
stateDiagram-v2
    [*] --> Unapproved
    Unapproved --> Approved: matching label or authorized command
    Approved --> Dismissed: label removed or unapprove command
    Approved --> Stale: head commit changes
    Stale --> Approved: reapprove enabled or authorized command
    Dismissed --> Approved: later matching event or command
```

The same flow in words:

- `opened`, `reopened`, and `labeled` may create a label-driven approval.
- Every configured eligibility category must pass before that approval.
- Removing any configured approval label dismisses active Stampbot approvals.
- A new head makes an old approval stale. `reapprove` decides whether a
  `synchronize` event may create a fresh review.
- An authorized ChatOps command may approve the current head or dismiss active
  Stampbot reviews.

This is only Stampbot's state machine. GitHub applies branch rules, dismissal
settings, and merge requirements after it.

## Policy boundary

For every event, Stampbot looks for policy in this order:

1. `stampbot.toml` on the target repository's default branch;
2. `stampbot.toml` in the owner's `.github` repository, for organization-owned
   repositories; and
3. defaults from the running service.

The first file wins. Repository and organization files are not merged.

A missing file moves lookup to the next source. A GitHub read failure currently
falls back to service defaults and records a load error. A readable but invalid
file stops automation for that event.

That distinction is historical and visible, not magical. Operators who need a
strict fallback should choose conservative service defaults and alert on
`stampbot_repo_config_loads_total{status="error"}`.

Repository title patterns cross a smaller boundary inside this flow. A
maintainer supplies the expression, while any pull request author may supply
the title. Stampbot bounds pattern count and length, caps title length, applies
a per-pattern timeout, and runs matching outside the event loop. A timeout
fails closed.

## Components

| Component | Owns |
| --- | --- |
| `stampbot/main.py` | HTTP routes, body limits, setup gates, and request metrics |
| `stampbot/webhook_handler.py` | Event routing, policy decisions, ChatOps, and review lifecycle |
| `stampbot/github_client.py` | App authentication, installation clients, retries, and GitHub calls |
| `stampbot/config.py` | Service settings, repository defaults, TOML parsing, and policy validation |
| `stampbot/manifest.py` | Trusted setup URLs and GitHub App manifest creation |
| `stampbot/metrics.py` | Prometheus metric definitions and the dedicated listener lifecycle |
| `stampbot/telemetry.py` | Optional OpenTelemetry export and span helpers |
| `stampbot/version.py` | One runtime version shared by HTTP, metrics, and traces |
| `charts/stampbot/` | Kubernetes packaging and deployment policy |

The [interface reference](reference.md) describes the surfaces these components
expose. The [configuration reference](configuration.md) describes their inputs.

## Trust boundaries

The webhook body is untrusted until its HMAC-SHA256 signature passes a
constant-time comparison. The body stays untrusted data after that; the
signature proves GitHub sent it, not that repository content is safe.

Repository policy has the trust level of the default branch that holds it.
Anyone who can change that file can change when Stampbot approves in that
repository.

The App private key and webhook secret are credentials. They belong in a secret
store, never in repository policy, logs, examples, or issue reports.
Installation tokens are short-lived and installation-scoped, but they still
carry the App permissions listed in the
[configuration reference](configuration.md#github-app-permissions).

`/setup` returns generated credentials during the manifest flow. It is disabled
by default, uses only the configured trusted base URL, and closes automatically
after credentials are present. Reopening it requires a second explicit flag.
Metrics use a disabled-by-default listener on a separate port. That listener
has no application-level authentication, so bind it only to loopback or a
private monitoring network. The public HTTP listener never serves `/metrics`.

Endpoint labels use route templates, and unmatched paths collapse to one value.
Raw attacker-controlled paths never become labels.

## Deliberate limits

Stampbot creates and dismisses reviews made by its own App identity. It doesn't
merge, edit branch protection, grant repository access, or impersonate a native
`CODEOWNERS` entry.

Those limits keep its decision visible and leave final control with GitHub's
rules and the people who own the repository.
