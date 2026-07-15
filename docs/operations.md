# Operations runbook

Use this runbook when Stampbot is running but setup, webhooks, reviews, or
metrics aren't behaving as expected.

## Start with the service

Set `BASE_URL` to the public Stampbot origin:

```bash
BASE_URL=https://stampbot.example.com
curl -fsS "${BASE_URL}/health"
curl -fsS "${BASE_URL}/ready"
```

Read the two results separately:

- `/health` proves only that the process can answer HTTP.
- `/ready` reports whether the process has credentials or can still serve setup.

A production instance should be ready and report `configured: true` with
`setup_enabled: false`.

For Kubernetes, check the workload and its recent logs:

```bash
kubectl get pods --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
kubectl logs deployment/stampbot --namespace stampbot --tail=100
kubectl get secret stampbot-github --namespace stampbot
```

`kubectl get secret` confirms the object exists; it doesn't print secret values.

## Read the GitHub delivery

In the GitHub App settings, open **Advanced**, then **Recent Deliveries**. Record
the delivery ID, event, action, response code, and sanitized response body.

| Response | Meaning | Next check |
| --- | --- | --- |
| `200` with `status: success` or `ok` | Stampbot completed the action. | Inspect the pull request timeline. |
| `200` with `status: ignored` | The event was valid but didn't call for an action. | Check labels, command text, filters, and `reapprove`. |
| `200` with `status: error` | The handler found missing fields, invalid policy, or a failed GitHub operation. | Read `message` and correlate the delivery with logs and metrics. |
| `400` | The event header is missing or the body isn't JSON. | Redeliver from GitHub. Repeated failures need the delivery metadata. |
| `401` | The signature is missing or the webhook secret doesn't match. | Make the App and `STAMPBOT_WEBHOOK_SECRET` use the same secret. |
| `413` | The delivery exceeds the 1 MiB body limit. | Record the event and delivery ID, then report a reproducible GitHub payload. |
| `503` | One or more App credentials are missing. | Check App ID, private key, and webhook secret in the runtime. |
| `500` | Event handling raised unexpectedly. | Find the matching sanitized error in the service logs. |

Redeliver the same event after fixing a route, secret, permission, or policy.
That keeps the input constant.

## Check credentials and setup

`/ready` returns `503` only when credentials are incomplete and setup is
disabled. Check the runtime for all three variables:

- `STAMPBOT_APP_ID`;
- `STAMPBOT_PRIVATE_KEY`; and
- `STAMPBOT_WEBHOOK_SECRET`.

If the private-key value is a path, it must select a regular file no larger than
64 KiB inside the process or container. Its PEM header and footer must match.

First-run setup requires both `STAMPBOT_SETUP_ENABLED=true` and a trusted
`STAMPBOT_BASE_URL`. Request host and forwarding headers are ignored. After
credentials are configured, setup returns `403` automatically. Keep
`STAMPBOT_SETUP_ENABLED=false` and `STAMPBOT_SETUP_ALLOW_CONFIGURED=false` in
normal operation.

During first-run setup only, this status check is available:

```bash
curl -fsS "${BASE_URL}/setup/status"
```

It returns only `configured` and `setup_enabled`; it never returns the App ID.
If deliberate reprovisioning is necessary, set both setup flags to `true` for
the shortest possible maintenance window, confirm `STAMPBOT_BASE_URL`, and turn
both flags off after storing the replacement credentials.

## Check GitHub App access

The [permission table](configuration.md#github-app-permissions) is the source of
truth.

| Symptom | Likely access problem |
| --- | --- |
| Approval, lookup, or dismissal fails | Pull requests isn't read and write. |
| Repository policy isn't found | Contents isn't read, or the App isn't installed on that repository. |
| Organization fallback isn't found | The App isn't installed on `ORG/.github`. |
| Team filters reject every author | Members isn't read, the team slug is wrong, or the repository isn't organization-owned. |
| An authorized ChatOps user is rejected | Administration isn't read. |
| Comments never reach Stampbot | Comment event subscriptions are missing. |

After changing App permissions, approve the new permissions on the installation.
Then redeliver a recent webhook.

## Inspect repository policy

Set the repository coordinates first:

```bash
OWNER=example-org
REPOSITORY=example-repo
```

Ask GitHub for each possible file:

```bash
gh api "repos/${OWNER}/${REPOSITORY}/contents/stampbot.toml" --jq .download_url
gh api "repos/${OWNER}/.github/contents/stampbot.toml" --jq .download_url
```

The first file wins. The organization file is checked only when the first one
is absent and the owner is an organization.

To validate a file with Stampbot's parser, run this from a Stampbot source
checkout with its virtual environment installed:

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

If GitHub can't read a policy file, Stampbot uses its service defaults. If it
reads invalid TOML, an invalid permission, or an invalid or out-of-bounds title
pattern list, Stampbot stops automation for that event.

## Diagnose label approval

| Symptom | Check |
| --- | --- |
| A labeled pull request isn't approved | Confirm the current labels include an `approval_labels` value and every configured filter passes. |
| Logs say an approval label doesn't exist | Create that repository label or remove it from policy. |
| Removing one approval label dismisses the review while another remains | This is current behavior. Any configured approval-label removal dismisses active Stampbot approvals. |
| A new commit doesn't receive approval | `reapprove` defaults to `false`. Enable it only when a new head should inherit the policy decision. |
| Repeated events don't add another review | This is expected when an active Stampbot approval already covers the current head. |
| `synchronize` still does nothing with `reapprove = true` | Stampbot needs a previous App review and a configured approval label on the pull request. |
| The response mentions a title-pattern safety limit or evaluation failure | A pattern exceeded its 10 ms match budget or the engine failed. Reproduce with the policy validator and a sanitized title, then simplify or split the pattern. Stampbot intentionally doesn't approve that event. |

Eligibility filters apply to label-driven approval. They don't limit an
authorized ChatOps approval.

## Diagnose ChatOps

| Symptom | Check |
| --- | --- |
| `@stampbot help` does nothing | The comment must be on a pull request and its webhook must reach Stampbot. |
| Approve or unapprove is ignored | Compare the commenter with `chatops_required_permission`. |
| The response says `Unknown command` | Use a word listed in `approve_commands` or `unapprove_commands`. |
| A custom command is cut short | Commands parse as one `\w+` word; avoid hyphens and spaces. |
| A long comment is ignored | Retry with a comment under 65,536 characters. |
| Approval isn't blocked by label filters | This is expected. ChatOps authorization uses repository permission. |

`@stampbot help` doesn't run the collaborator permission check. It reports the
effective commands and policy so readers can discover the repository's setup.

## Use metrics to narrow the failure

Port-forward the Kubernetes service when metrics aren't public:

```bash
kubectl port-forward svc/stampbot 8000:80 --namespace stampbot
curl -fsS http://127.0.0.1:8000/metrics
```

| Metric | Question it answers |
| --- | --- |
| `stampbot_http_requests_total` | Is traffic reaching the expected path, and which HTTP statuses return? |
| `stampbot_webhook_signature_validations_total` | Are signatures failing? |
| `stampbot_webhook_events_total` | Which authenticated events and actions reach the handler? |
| `stampbot_repo_config_loads_total` | Is policy found, defaulted, or failing? |
| `stampbot_pr_approvals_total` | Are approval attempts succeeding? |
| `stampbot_pr_dismissals_total` | Are dismissal attempts succeeding? |
| `stampbot_chatops_commands_total` | Are commands parsed, forbidden, or ignored? |
| `stampbot_github_api_requests_total` | Which GitHub operation is failing? |
| `stampbot_github_api_rate_limit_remaining` | Is an installation close to its core API limit? |
| `stampbot_errors_total` | Which application error category is rising? |

The app doesn't authenticate `/metrics`. Keep it behind an operator boundary
when the public should not see it.

## Respond to GitHub API failures

GitHub calls have a 30-second timeout and retry server errors with exponential
backoff. Authorization errors and rate limits still need operator action.

When the remaining rate limit is low:

1. Look for webhook redelivery loops or a sudden increase in event volume.
2. Find repeated file, label, review, or permission lookups in the operation
   metric and logs.
3. Stop unnecessary redeliveries.
4. Wait for the installation limit to reset.

Adding replicas doesn't increase a GitHub App installation's rate limit.

## Roll back a deployment

For Helm:

```bash
helm history stampbot --namespace stampbot
helm rollback stampbot PREVIOUS_REVISION --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
helm test stampbot --namespace stampbot --logs
```

For Cloud Run:

```bash
gcloud run revisions list --service stampbot --region us-central1
gcloud run services update-traffic stampbot \
  --region us-central1 \
  --to-revisions PREVIOUS_REVISION=100
```

A deployment rollback doesn't restore GitHub App settings, Kubernetes Secrets,
or Secret Manager versions. Restore those separately when they caused the
failure.

## Escalate with useful evidence

Include:

- deployment mode and Stampbot version;
- image digest and chart version, when applicable;
- GitHub App permission and event-subscription state;
- delivery ID, event, action, HTTP status, and sanitized response body;
- the effective `stampbot.toml` with sensitive names removed;
- relevant logs around the delivery; and
- error, webhook, GitHub API, policy-load, and rate-limit metrics from the same
  window.

Remove tokens, private keys, webhook secrets, cloud identifiers, customer data,
and private repository content before sharing any evidence.
