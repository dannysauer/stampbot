# Deploy Stampbot to Google Cloud Run

This guide deploys Stampbot to Cloud Run with the repository's
`Deploy to Cloud Run` GitHub Actions workflow.

The workflow deploys `docker.io/stampbot/stampbot:<tag>` and keeps the Cloud Run
service's existing environment variable and Secret Manager configuration. Configure
Stampbot credentials on the Cloud Run service before sending production webhooks.

## Prerequisites

- Google Cloud project with billing enabled.
- `gcloud` CLI authenticated with permission to manage Cloud Run, IAM, Workload Identity
  Federation, and Secret Manager.
- Repository admin access for GitHub Actions secrets and variables.
- A Docker Hub token stored as GitHub Actions secret `DOCKERHUB_TOKEN` for the release
  workflow.
- GitHub App credentials from the setup wizard or manual app creation.

Use placeholders consistently:

```bash
PROJECT_ID=example-project
PROJECT_NUMBER=123456789012
REGION=us-central1
SERVICE_NAME=stampbot
REPOSITORY=dannysauer/stampbot
```

## Enable APIs

```bash
gcloud config set project "${PROJECT_ID}"
gcloud services enable \
  run.googleapis.com \
  iamcredentials.googleapis.com \
  secretmanager.googleapis.com
```

## Create a Deploy Service Account

```bash
gcloud iam service-accounts create github-actions-deployer \
  --display-name="GitHub Actions Cloud Run Deployer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:github-actions-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:github-actions-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

## Configure Workload Identity Federation

```bash
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --display-name="GitHub Actions Pool"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${REPOSITORY}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud iam service-accounts add-iam-policy-binding \
  "github-actions-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${REPOSITORY}"
```

## Configure GitHub Actions Variables

In the GitHub repository, open **Settings > Secrets and variables > Actions > Variables**
and set:

| Variable | Required | Value |
| --- | --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Yes | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | Yes | `github-actions-deployer@PROJECT_ID.iam.gserviceaccount.com` |
| `CLOUDRUN_REGION` | No | Cloud Run region, default `us-central1`. |
| `CLOUDRUN_SERVICE_NAME` | No | Cloud Run service name, default `stampbot`. |

## Create Runtime Secrets

Store GitHub App credentials in Secret Manager:

```bash
printf '%s' '123456' | gcloud secrets create stampbot-app-id --data-file=-
gcloud secrets create stampbot-private-key --data-file=./private-key.pem
printf '%s' 'replace-with-webhook-secret' | \
  gcloud secrets create stampbot-webhook-secret --data-file=-
```

Grant the Cloud Run runtime service account access to the secrets. If you use the default
runtime service account, get it first:

```bash
RUNTIME_SERVICE_ACCOUNT="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true
)"

if [ -z "${RUNTIME_SERVICE_ACCOUNT}" ]; then
  RUNTIME_SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
fi

for SECRET in stampbot-app-id stampbot-private-key stampbot-webhook-secret; do
  gcloud secrets add-iam-policy-binding "${SECRET}" \
    --member="serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
    --role=roles/secretmanager.secretAccessor
done
```

## First Deployment

Run the GitHub Actions `Deploy to Cloud Run` workflow manually and provide an image tag,
or publish an app release. The workflow uses these deployment flags:

- `--port=8000`
- `--allow-unauthenticated`

Unauthenticated ingress is required for GitHub webhooks. Disable the setup endpoint after
the one-time setup flow because unauthenticated users can otherwise reach `/setup`.

## Configure Stampbot Environment

After the service exists, attach secrets and source-backed `STAMPBOT_*` settings:

```bash
SERVICE_URL="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --format='value(status.url)'
)"
SECRET_MOUNTS="STAMPBOT_APP_ID=stampbot-app-id:latest,STAMPBOT_PRIVATE_KEY=stampbot-private-key:latest,STAMPBOT_WEBHOOK_SECRET=stampbot-webhook-secret:latest" # pragma: allowlist secret

gcloud run services update "${SERVICE_NAME}" \
  --region "${REGION}" \
  --update-secrets="${SECRET_MOUNTS}" \
  --update-env-vars="STAMPBOT_SETUP_ENABLED=false,STAMPBOT_BASE_URL=${SERVICE_URL},STAMPBOT_LOG_FORMAT=json"
```

Set the GitHub App webhook URL to:

```text
SERVICE_URL/webhook
```

For a custom domain, set `STAMPBOT_BASE_URL` to the custom HTTPS origin instead of the
`run.app` URL.

## One-Time Setup Flow

If you do not already have GitHub App credentials, temporarily enable setup:

```bash
SERVICE_URL="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --format='value(status.url)'
)"

gcloud run services update "${SERVICE_NAME}" \
  --region "${REGION}" \
  --update-env-vars="STAMPBOT_SETUP_ENABLED=true,STAMPBOT_BASE_URL=${SERVICE_URL}"
```

Open `SERVICE_URL/setup`, create the GitHub App, store the returned credentials in Secret
Manager, then disable setup:

```bash
gcloud run services update "${SERVICE_NAME}" \
  --region "${REGION}" \
  --update-env-vars="STAMPBOT_SETUP_ENABLED=false,STAMPBOT_BASE_URL=${SERVICE_URL}"
```

## Verification

```bash
SERVICE_URL="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --format='value(status.url)'
)"

curl -fsS "${SERVICE_URL}/health"
curl -fsS "${SERVICE_URL}/setup/status"
```

Expected production setup status:

```json
{
  "configured": true,
  "setup_enabled": false,
  "app_id": "123456"
}
```

Check logs:

```bash
gcloud run services logs read "${SERVICE_NAME}" \
  --region "${REGION}" \
  --limit=50
```

In the GitHub App settings, send a `ping` redelivery. Expected response:

```json
{
  "status": "ok",
  "message": "pong"
}
```

## Rollback

List revisions:

```bash
gcloud run revisions list \
  --service "${SERVICE_NAME}" \
  --region "${REGION}"
```

Route all traffic to a previous revision:

```bash
gcloud run services update-traffic "${SERVICE_NAME}" \
  --region "${REGION}" \
  --to-revisions "REVISION_NAME=100"
```

Rollback changes only the Cloud Run revision. It does not revert GitHub App webhook URLs,
GitHub App permissions, or Secret Manager secret versions. Restore those separately if
they changed.

## Troubleshooting

| Symptom | Check | Remediation |
| --- | --- | --- |
| `/webhook` returns `503` | `/setup/status` has `configured: false`. | Attach `STAMPBOT_APP_ID`, `STAMPBOT_PRIVATE_KEY`, and `STAMPBOT_WEBHOOK_SECRET` secrets. |
| GitHub delivery returns `401` | Webhook secret mismatch. | Update Secret Manager and GitHub App webhook secret to the same value, then redeploy or refresh the revision. |
| Setup creates wrong callback or webhook URL | `STAMPBOT_BASE_URL` missing or points to an internal host. | Set `STAMPBOT_BASE_URL` to the public HTTPS origin. |
| Deployment workflow succeeds but config does not change | Workflow preserves existing Cloud Run service config. | Run `gcloud run services update` to change env vars or secrets. |
| GitHub cannot reach Cloud Run | Service not unauthenticated or webhook URL wrong. | Confirm `--allow-unauthenticated` and set webhook URL to `https://.../webhook`. |

More troubleshooting steps are in [operations.md](operations.md).

## Cleanup

```bash
gcloud run services delete "${SERVICE_NAME}" --region "${REGION}"
gcloud iam service-accounts delete "github-actions-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam workload-identity-pools delete github-pool --location=global
gcloud secrets delete stampbot-app-id
gcloud secrets delete stampbot-private-key
gcloud secrets delete stampbot-webhook-secret
```
