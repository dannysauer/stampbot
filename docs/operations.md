# Operate Stampbot

Use this runbook when Stampbot is unhealthy, unready, or not changing a review
as expected. Keep one GitHub delivery ID throughout an investigation so logs,
responses, and metrics describe the same input.

## Check the process

Set the public origin, then check liveness and readiness separately:

```bash
BASE_URL=https://stampbot.example.com
curl -fsS "${BASE_URL}/health"
curl -fsS "${BASE_URL}/ready"
```

- `/health` proves that the HTTP process answers.
- `/ready` proves that credentials are complete or first-run setup is available.

A production instance should report `configured: true` and
`setup_enabled: false`. An unconfigured instance with setup disabled returns
`503` from `/ready`.

For Kubernetes, inspect the Deployment without printing Secret data:

```bash
kubectl rollout status deployment/stampbot --namespace stampbot
kubectl get pods --namespace stampbot
kubectl logs deployment/stampbot --namespace stampbot --tail=100
kubectl get secret stampbot-github --namespace stampbot
```

The last command confirms that the Secret object exists. It does not prove that
all three required keys contain usable values.

## Follow one GitHub delivery

Open the GitHub App's **Advanced** settings, then open **Recent Deliveries**.
Record the delivery ID, event, action, response status, and a redacted response
body. Stampbot logs the same ID as `delivery_id` and records it on the trace as
`github.delivery_id`, so one delivery can be followed from GitHub to Loki and
Tempo.

| Response | Meaning | Next check |
| --- | --- | --- |
| `200`, `success` or `ok` | Stampbot completed the handler. | Read the pull request timeline. |
| `200`, `ignored` | The event was valid but did not require an action. | Check the action, labels, command, filters, and `reapprove`. |
| `200`, `error` | Policy, payload fields, or a GitHub operation failed. | Read the response message and matching logs. |
| `400` | The event header is missing or the body is not JSON. | Redeliver the unchanged event. |
| `401` | The signature is missing or invalid. | Compare the App webhook secret with the runtime Secret. |
| `413` | The body exceeds 1 MiB. | Save only the delivery metadata and report a reproducible case. |
| `503` | One or more App credentials are missing. | Check App ID, private key, and webhook secret. |
| `500` | The handler raised unexpectedly. | Find the sanitized exception for the delivery window. |

After correcting one cause, redeliver the same event. Changing the payload at
the same time makes the result harder to compare.

## Check credentials and setup

Stampbot needs all three values:

- `STAMPBOT_APP_ID`;
- `STAMPBOT_PRIVATE_KEY`; and
- `STAMPBOT_WEBHOOK_SECRET`.

A private-key value may contain PEM text or select a regular file. A selected
file must be no larger than 64 KiB and must contain a complete private-key PEM
envelope.

First-run setup also needs both values below:

```dotenv
STAMPBOT_SETUP_ENABLED=true
STAMPBOT_BASE_URL=https://stampbot.example.com
```

The base URL is trusted configuration. Request host and forwarding headers do
not change the manifest callback or webhook URL.

Check setup only during the provisioning window:

```bash
curl -fsS "${BASE_URL}/setup/status"
```

The response contains only `configured` and `setup_enabled`. Setup closes after
credentials appear. Keep `STAMPBOT_SETUP_ENABLED=false` and
`STAMPBOT_SETUP_ALLOW_CONFIGURED=false` during normal operation.

For deliberate reprovisioning, set both flags to `true` for the shortest useful
window. Confirm `STAMPBOT_BASE_URL` first, store the replacement credentials,
then disable both flags.

## Check GitHub App access

Use the [permission table](configuration.md#github-app-permissions) as the
source of truth.

| Symptom | Check |
| --- | --- |
| Approval lookup, creation, or dismissal fails | Pull requests has read and write access. |
| Repository policy is missing | Contents has read access and the App is installed on the repository. |
| Organization fallback is missing | The App is installed on the organization's `.github` repository. |
| Every team-filtered author is rejected | Members has read access and the team slug is correct. |
| An authorized ChatOps user is rejected | Administration has read access. |
| Comments never arrive | Issue comment and review comment events are subscribed. |

Approve changed App permissions on each installation before redelivering the
event.

## Inspect repository policy

Set the repository coordinates:

```bash
OWNER=example-org
REPOSITORY=example-repo
```

Check both policy locations:

```bash
gh api "repos/${OWNER}/${REPOSITORY}/contents/stampbot.toml" --jq .download_url
gh api "repos/${OWNER}/.github/contents/stampbot.toml" --jq .download_url
```

The repository file wins. Stampbot checks the organization file only when the
repository file is absent and the owner is an organization.

Validate a downloaded file from a Stampbot source checkout:

```bash
POLICY_FILE=/path/to/stampbot.toml
.venv/bin/python - "${POLICY_FILE}" <<'PY'
from pathlib import Path
import sys

from stampbot.config import RepoConfig

RepoConfig.from_toml(Path(sys.argv[1]).read_text())
print("stampbot.toml is valid")
PY
```

A missing policy file moves lookup to the next source. GitHub also returns a
repository-level `404` when `OWNER/.github` doesn't exist or the App installation
doesn't include it. Stampbot treats that optional repository as unavailable and
uses service defaults.

A failure reading the target repository's policy stops automation for that
event. Once GitHub makes the organization repository available to the App, a
failure reading its policy does too. A readable but invalid file also stops
automation. Stampbot records these failures as policy-load errors.

## Diagnose label approval

| Symptom | Check |
| --- | --- |
| A labeled pull request is not approved | A current label appears in `approval_labels`, and every configured filter category passes. If the label was on the pull request when it was created, the `labeled` delivery creates the approval and the `opened` delivery is ignored. Redeliver the `labeled` delivery, or remove and re-add the label. A pull request converted from a labeled issue gets no `labeled` delivery; re-add the label. |
| An approval label is reported missing | Create that label or remove it from policy. |
| A policy edit has no effect | Wait `STAMPBOT_REPO_CONFIG_CACHE_SECONDS` (default 300) or restart the replicas. A corrected file replaces an invalid one immediately, because errors are never cached. |
| Removing one approval label dismisses the review | This is current behavior, even when another approval label remains. |
| A new commit is not approved | `reapprove` defaults to `false`. |
| A repeated event creates no review | An active Stampbot approval already covers the current head. |
| `synchronize` does nothing with `reapprove = true` | A prior App review and a configured approval label must both exist. |
| Title evaluation fails safely | Simplify the expression or split it. Each pattern has a 10 ms budget. |

Title matching accepts at most 20 patterns of 256 characters. It evaluates at
most 256 title characters. A timeout or engine error never creates an approval.

## Diagnose ChatOps

| Symptom | Check |
| --- | --- |
| `@stampbot help` does nothing | The comment belongs to a pull request and its webhook reached Stampbot. |
| Approve or unapprove is forbidden | The commenter meets `chatops_required_permission`. |
| The command is unknown | Its word appears in `approve_commands` or `unapprove_commands`. |
| A custom command is cut short | Commands contain one `\w+` word; avoid spaces and hyphens. |
| A long comment is ignored | Retry below 65,536 characters. |
| ChatOps ignores label filters | This is intentional; repository permission authorizes ChatOps. |

`@stampbot help` does not require the collaborator permission check. It reports
the effective command and policy names so contributors can discover them.

## Inspect metrics privately

The public HTTP listener does not serve metrics. A request to its `/metrics`
path returns `404`.

For a source process, enable the separate loopback listener:

```bash
export STAMPBOT_METRICS_ENABLED=true
export STAMPBOT_METRICS_HOST=127.0.0.1
export STAMPBOT_METRICS_PORT=9090
.venv/bin/python -m stampbot
```

Query it from the same host:

```bash
curl -fsS http://127.0.0.1:9090/metrics
```

For Helm, set `metrics.enabled=true`. The chart creates a separate ClusterIP
Service that the Ingress never selects. Start a port-forward and leave it
running:

```bash
kubectl port-forward service/stampbot-metrics 9090:9090 --namespace stampbot
```

From another terminal:

```bash
curl -fsS http://127.0.0.1:9090/metrics
```

| Metric | Question |
| --- | --- |
| `stampbot_http_requests_total` | Which route templates and statuses are active? |
| `stampbot_webhook_signature_validations_total` | Are signature checks failing? |
| `stampbot_webhook_events_total` | Which authenticated events reach the handler? |
| `stampbot_repo_config_loads_total` | Is policy found, defaulted, cached, or invalid? |
| `stampbot_pr_approvals_total` | Are approval attempts succeeding? |
| `stampbot_pr_dismissals_total` | Are dismissals succeeding? |
| `stampbot_chatops_commands_total` | Are commands accepted, forbidden, or ignored? |
| `stampbot_github_api_requests_total` | Which GitHub operation fails? |
| `stampbot_github_api_rate_limit_remaining` | Is an installation near its API limit? |
| `stampbot_errors_total` | Which application error class is rising? |

The metrics listener has no application authentication. Bind it to loopback or
a private monitoring network.

## Check telemetry transport

OTLP uses TLS by default. For a collector with a private certificate authority,
set `OTEL_EXPORTER_OTLP_CERTIFICATE` to a mounted PEM CA path. The Helm chart can
mount that path from an existing Secret.

Use `STAMPBOT_OTEL_INSECURE=true` only for a plaintext collector on an isolated
development network. An HTTPS endpoint remains secure even when that flag is
set.

## Check Kubernetes policy

The chart leaves NetworkPolicy disabled because peer labels differ by cluster.
Before enabling it, compare its namespace and pod selectors with the labels in
your ingress controller, Prometheus, DNS, and OTLP collector.

The defaults allow HTTP from Stampbot and ingress-nginx peers, metrics from
Prometheus, DNS, TCP 443, and a labeled local collector. Kubernetes
NetworkPolicy cannot restrict TCP 443 by DNS name. Use a CNI or egress gateway
with hostname policy when destination control is required.

The default rules target the named `metrics` container port, so changing
`metrics.port` preserves them. Replace the raw rules only when peer selectors
or additional traffic differ from the defaults. The chart also disables
Kubernetes API token mounts and limits `/tmp` to 64 MiB by default.

## Respond to GitHub API failures

GitHub calls use a 30-second timeout. Stampbot retries server errors with
exponential backoff; authorization and rate-limit failures need operator action.

Each replica reuses one installation token until GitHub is about to expire it,
and reads the remaining limit from response headers. In steady state
`stampbot_github_api_requests_total{operation="get_token"}` rises about once per
hour per active installation. A faster rise points at restarts, new
installations, or token failures rather than event volume.

When the remaining limit is low:

1. Stop webhook redelivery loops.
2. Find repeated operations in logs and `stampbot_github_api_requests_total`.
3. Reduce avoidable event volume.
4. Wait for the installation limit to reset.

Adding replicas does not increase a GitHub App installation's rate limit.

## Roll back

For Helm:

```bash
helm history stampbot --namespace stampbot
helm rollback stampbot PREVIOUS_REVISION --namespace stampbot --wait
kubectl rollout status deployment/stampbot --namespace stampbot
helm test stampbot --namespace stampbot --logs --timeout 2m
```

For Cloud Run:

```bash
gcloud run revisions list --service stampbot --region us-central1
gcloud run services update-traffic stampbot \
  --region us-central1 \
  --to-revisions PREVIOUS_REVISION=100
```

A deployment rollback does not restore GitHub App settings, Kubernetes Secrets,
Secret Manager versions, or repository policy. Restore those separately when
they caused the incident.

## Escalate with safe evidence

Include:

- deployment mode and reported Stampbot version;
- image digest and chart version, when applicable;
- GitHub App permission and event-subscription state;
- delivery ID, event, action, status, and redacted response;
- a sanitized effective policy;
- logs around the delivery; and
- related error, policy, GitHub API, webhook, and rate-limit metrics.

Remove tokens, private keys, webhook secrets, cloud account identifiers,
customer data, and private repository content before sharing evidence.
