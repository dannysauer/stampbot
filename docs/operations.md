# Operations and Troubleshooting

Use this runbook when Stampbot is deployed but approvals, dismissals, setup, webhooks, or
metrics are not behaving as expected.

## Fast Checks

Start with these checks:

```bash
curl -fsS https://stampbot.example.com/health
curl -fsS https://stampbot.example.com/setup/status
```

For Kubernetes:

```bash
kubectl get pods --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
kubectl logs deployment/stampbot --namespace stampbot --tail=100
kubectl get secret stampbot-github --namespace stampbot
```

Expected healthy signals:

- `/health` returns `{"status":"healthy"}`.
- `/setup/status` returns `configured: true` in production.
- Pods are Ready.
- Logs include `GitHub App credentials configured successfully`.

## Webhook Delivery Triage

In the GitHub App settings, open **Advanced** and inspect Recent Deliveries. Record the
delivery ID, event, action, response code, and response body.

| Response | Likely cause | Remediation |
| --- | --- | --- |
| `200` with `status: success` | Stampbot handled the event. | Check the PR timeline for the approval, dismissal, or help comment. |
| `200` with `status: ignored` | Event was valid but did not match policy. | Check labels, command text, `chatops_enabled`, filters, and `reapprove`. |
| `400 Missing X-GitHub-Event header` | Request did not include GitHub's event header. | Verify the request came from GitHub webhooks. |
| `400 Invalid JSON payload` | Payload could not be parsed. | Redeliver from GitHub. If repeated, capture the delivery body for maintainers. |
| `401 Invalid signature` | Webhook secret mismatch or missing signature. | Rotate or re-enter the GitHub App webhook secret and update `STAMPBOT_WEBHOOK_SECRET`. |
| `413 Request body too large` | Payload exceeded the 1 MiB limit. | Capture the event type and delivery ID; file an issue if this happens for normal GitHub events. |
| `503 Stampbot not configured` | App ID, private key, or webhook secret is missing. | Set `STAMPBOT_APP_ID`, `STAMPBOT_PRIVATE_KEY`, and `STAMPBOT_WEBHOOK_SECRET`; restart the service. |
| `500 Internal server error` | Handler or GitHub API call raised unexpectedly. | Check logs for sanitized error context and GitHub API status. |

Redeliver after changing secrets, permissions, or route configuration.

## GitHub App Permission Checks

The required permissions and event subscriptions are documented in
[configuration.md](configuration.md#github-app-permissions).

Common symptoms:

| Symptom | Likely missing permission |
| --- | --- |
| Approval or dismissal fails | Pull requests: read and write |
| `stampbot.toml` is ignored even though it exists | Contents: read |
| Team filters always fail | Members: read |
| ChatOps approval says permissions are insufficient for authorized users | Administration: read |
| ChatOps comments are not received | Issue comment and pull request review comment events |

After changing GitHub App permissions, reinstall or approve the updated permissions for
the app installation, then redeliver a recent webhook.

## Repository Policy Triage

Check the effective repository policy:

```bash
gh api repos/OWNER/REPO/contents/stampbot.toml --jq .download_url
gh api repos/OWNER/.github/contents/stampbot.toml --jq .download_url
```

If neither file exists, Stampbot uses the defaults from the running service.

For invalid `stampbot.toml`:

- On a newly opened PR, Stampbot posts a review comment with the validation error.
- On other events, Stampbot logs the error and takes no approval action.

Validate locally before committing:

```bash
python - <<'PY'
from pathlib import Path
from stampbot.config import RepoConfig

RepoConfig.from_toml(Path("stampbot.toml").read_text())
print("stampbot.toml is valid")
PY
```

## Label Approval Problems

| Symptom | Check | Remediation |
| --- | --- | --- |
| PR with label is not approved | `approval_labels`, `auto_approve_on_label`, required filters. | Add an approval label and satisfy every configured filter. |
| Approval label exists in config but not in GitHub | Logs warn `Approval label ... not found`. | Create the label in the repository or remove it from config. |
| Removing a label does not dismiss approval | PR may still have another configured approval label. | Remove all configured approval labels when dismissal is desired. |
| New commits do not get fresh approval | `reapprove` defaults to `false`. | Set `reapprove = true` in `stampbot.toml` when this is intentional. |
| Duplicate events do not add duplicate approvals | This is expected. | Stampbot skips when it already has an active approval for the current PR head. |

## ChatOps Problems

| Symptom | Likely cause | Remediation |
| --- | --- | --- |
| `@stampbot help` does nothing | Comment was not on a PR, did not include `@stampbot`, or webhook event was not delivered. | Check GitHub Recent Deliveries for `issue_comment` or `pull_request_review_comment`. |
| Approval command is ignored | User lacks `chatops_required_permission`. | Lower the threshold or grant the user enough repository permission. |
| Command returns `Unknown command` | Command word is not listed in `approve_commands` or `unapprove_commands`. | Use the configured command or update `stampbot.toml`. |
| Long comment ignored | Comment body exceeded 64 KiB. | Retry with a short command comment. |

## Metrics

Stampbot serves Prometheus metrics at `/metrics` on the main HTTP port.

Useful metrics:

| Metric | Use |
| --- | --- |
| `stampbot_http_requests_total` | Confirm GitHub is reaching the service and inspect status codes. |
| `stampbot_webhook_events_total` | Count events by GitHub event and action. |
| `stampbot_webhook_signature_validations_total` | Detect invalid webhook signatures. |
| `stampbot_pr_approvals_total` | Count approval attempts by trigger and status. |
| `stampbot_pr_dismissals_total` | Count dismissal attempts by trigger and status. |
| `stampbot_chatops_commands_total` | Count ChatOps commands by command and status. |
| `stampbot_repo_config_loads_total` | Detect missing or invalid repository configuration. |
| `stampbot_github_api_requests_total` | Identify GitHub API operation failures. |
| `stampbot_github_api_rate_limit_remaining` | Watch remaining GitHub API rate limit by installation. |
| `stampbot_errors_total` | Count application error categories. |

Kubernetes port-forward example:

```bash
kubectl port-forward svc/stampbot 8000:80 --namespace stampbot
curl http://127.0.0.1:8000/metrics
```

## Rate Limits and GitHub API Failures

Stampbot logs GitHub API failures with sanitized errors and increments
`stampbot_github_api_requests_total`.

If rate limit remaining is low:

1. Check whether webhook redelivery or duplicate events are unusually high.
2. Check whether many repositories are missing labels or config, causing repeated lookup
   calls.
3. Wait for the GitHub App installation rate limit to reset.
4. Reduce noisy webhook redeliveries before scaling replicas.

## Deployment Rollback

For Helm:

```bash
helm history stampbot --namespace stampbot
helm rollback stampbot REVISION --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
```

For Cloud Run:

```bash
gcloud run revisions list --service stampbot --region us-central1
gcloud run services update-traffic stampbot \
  --region us-central1 \
  --to-revisions REVISION_NAME=100
```

Rollback does not restore external GitHub App settings or cloud Secret Manager versions.
Restore those separately when credentials or webhook URLs changed.

## Escalation Data

When opening an issue, include:

- Deployment mode: local, Docker, Helm, EKS, or Cloud Run.
- Stampbot version, container image digest, and chart version when applicable.
- GitHub App permission state and subscribed events.
- Webhook delivery ID, event, action, response code, and sanitized response body.
- Sanitized `stampbot.toml` or a statement that defaults are used.
- Relevant logs with secrets, tokens, private keys, and customer data removed.
- Metrics around the failure window, especially error, webhook, GitHub API, and rate limit
  metrics.
