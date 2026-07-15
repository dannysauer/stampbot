# Deploy Stampbot to Google Cloud Run

This guide creates a Cloud Run service once, then connects it to the
repository's `Deploy to Cloud Run` workflow. The workflow changes the image and
preserves the service's existing identities, secrets, and environment.

You will create two Google service accounts. GitHub Actions impersonates the
deployer account; the Stampbot container runs as the runtime account.

## Before you begin

You need:

- a Google Cloud project with billing enabled;
- `gcloud` authenticated as an administrator for that project;
- admin access to this GitHub repository's Actions variables;
- Stampbot App ID, private key, and webhook secret; and
- a published Stampbot image version.

Set the names used below:

```bash
PROJECT_ID=example-project
REGION=us-central1
SERVICE_NAME=stampbot
REPOSITORY=dannysauer/stampbot
APP_VERSION=1.11.0
DEPLOYER_NAME=github-actions-deployer
RUNTIME_NAME=stampbot-runtime

gcloud config set project "${PROJECT_ID}"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
DEPLOYER_SA="${DEPLOYER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
OPERATOR_EMAIL="$(gcloud config get-value account)"
```

`APP_VERSION` is a concrete example. Choose a release you have inspected before
production use.

## Enable the APIs

```bash
gcloud services enable \
  run.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  secretmanager.googleapis.com
```

## Create the service accounts

Create separate deployer and runtime identities:

```bash
gcloud iam service-accounts create "${DEPLOYER_NAME}" \
  --display-name="GitHub Actions Cloud Run deployer"

gcloud iam service-accounts create "${RUNTIME_NAME}" \
  --display-name="Stampbot Cloud Run runtime"
```

Let the deployer manage Cloud Run:

```bash
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role=roles/run.admin
```

Let the deployer act only as Stampbot's runtime identity:

```bash
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role=roles/iam.serviceAccountUser
```

The operator performing the first deployment also needs that access:

```bash
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="user:${OPERATOR_EMAIL}" \
  --role=roles/iam.serviceAccountUser
```

## Federate GitHub Actions

Create a Workload Identity pool and a provider restricted to this repository:

```bash
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --display-name="GitHub provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${REPOSITORY}'" \
  --issuer-uri=https://token.actions.githubusercontent.com
```

Allow identities from that repository to impersonate the deployer:

```bash
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${REPOSITORY}"
```

The repository condition matters. A pool-wide grant would let unrelated
identities in the pool attempt to use the deployer.

## Set GitHub Actions variables

Open **Settings > Secrets and variables > Actions > Variables** in the GitHub
repository.

| Variable | Value |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | The value of `DEPLOYER_SA` |
| `CLOUDRUN_REGION` | The value of `REGION`; optional, defaults to `us-central1` |
| `CLOUDRUN_SERVICE_NAME` | The value of `SERVICE_NAME`; optional, defaults to `stampbot` |

Replace `PROJECT_NUMBER` in the provider value with the numeric project number.

## Store runtime credentials

Create one Secret Manager secret for each credential. Each new secret starts
with version `1`:

```bash
printf '%s' '123456' | \
  gcloud secrets create stampbot-app-id --data-file=-

gcloud secrets create stampbot-private-key \
  --data-file=./private-key.pem

printf '%s' 'replace-with-the-github-webhook-secret' | \
  gcloud secrets create stampbot-webhook-secret --data-file=-
```

Give only the runtime identity access:

```bash
for SECRET in stampbot-app-id stampbot-private-key stampbot-webhook-secret; do
  gcloud secrets add-iam-policy-binding "${SECRET}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor
done
```

Cloud Run resolves secret environment variables when an instance starts.
Google recommends pinning a version instead of `latest` for this mode. The
[Cloud Run secret guide](https://docs.cloud.google.com/run/docs/configuring/services/secrets)
explains the difference between environment variables and mounted secrets.

## Create the service

Deploy the first revision with the dedicated runtime identity and secret version
`1`:

```bash
gcloud run deploy "${SERVICE_NAME}" \
  --region="${REGION}" \
  --image="docker.io/stampbot/stampbot:${APP_VERSION}" \
  --service-account="${RUNTIME_SA}" \
  --port=8000 \
  --allow-unauthenticated \
  --set-env-vars="STAMPBOT_SETUP_ENABLED=false,STAMPBOT_LOG_FORMAT=json" \
  --update-secrets="STAMPBOT_APP_ID=stampbot-app-id:1,STAMPBOT_PRIVATE_KEY=stampbot-private-key:1,STAMPBOT_WEBHOOK_SECRET=stampbot-webhook-secret:1" # pragma: allowlist secret
```

GitHub webhooks require an unauthenticated route. That also makes the app's
other routes public unless you put a path-aware proxy in front of Cloud Run.
In particular, decide whether `/metrics` is acceptable on that public surface.

Read the service URL and set it as Stampbot's public origin:

```bash
SERVICE_URL="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --format='value(status.url)'
)"

gcloud run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --update-env-vars="STAMPBOT_BASE_URL=${SERVICE_URL}"
```

Set the GitHub App webhook URL to `SERVICE_URL/webhook`. If you later add a
custom domain, update both the App and `STAMPBOT_BASE_URL`.

## Test the deployment workflow

Trigger the checked-in workflow with the same image version:

```bash
gh workflow run deploy-cloudrun.yml \
  --repo "${REPOSITORY}" \
  --field image_tag="${APP_VERSION}"
```

The workflow authenticates through federation and deploys
`docker.io/stampbot/stampbot:APP_VERSION`. It doesn't rewrite the service's
secret, runtime-account, or environment configuration.

## Verify the service

```bash
curl -fsS "${SERVICE_URL}/health"
curl -fsS "${SERVICE_URL}/ready"
curl -fsS "${SERVICE_URL}/setup/status"
```

Expected production state:

```json
{
  "configured": true,
  "setup_enabled": false,
  "app_id": "123456"
}
```

Read recent logs:

```bash
gcloud run services logs read "${SERVICE_NAME}" \
  --region="${REGION}" \
  --limit=50
```

Finally, redeliver a GitHub `ping` webhook. A healthy delivery returns:

```json
{"status":"ok","message":"pong"}
```

## Rotate a secret

Add a new version instead of replacing the existing one. This example rotates
the webhook secret:

```bash
printf '%s' 'the-new-github-webhook-secret' | \
  gcloud secrets versions add stampbot-webhook-secret --data-file=-

NEW_VERSION="$(
  gcloud secrets versions list stampbot-webhook-secret \
    --filter='state=ENABLED' \
    --sort-by='~createTime' \
    --limit=1 \
    --format='value(name)'
)"

gcloud run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --update-secrets="STAMPBOT_WEBHOOK_SECRET=stampbot-webhook-secret:${NEW_VERSION}" # pragma: allowlist secret
```

Update the GitHub App to the same webhook secret, then redeliver a recent event.
There is no atomic update across GitHub and Google Cloud, so plan for a short
window in which one side may reject signatures.

After verification, disable the old Secret Manager version according to your
retention policy.

## Roll back

List revisions:

```bash
gcloud run revisions list \
  --service="${SERVICE_NAME}" \
  --region="${REGION}"
```

Route all traffic to the last healthy revision:

```bash
gcloud run services update-traffic "${SERVICE_NAME}" \
  --region="${REGION}" \
  --to-revisions="PREVIOUS_REVISION=100"
```

Replace `PREVIOUS_REVISION` with a name from the revision list. Traffic rollback
doesn't restore a GitHub App setting or Secret Manager version.

## Troubleshoot

| Symptom | Check |
| --- | --- |
| Federation fails | Confirm the provider uses the numeric project number and the repository claim matches `owner/repo` exactly. |
| Deployment can't act as the runtime account | Confirm the deployer has `roles/iam.serviceAccountUser` on `RUNTIME_SA`. |
| A revision won't start | Confirm `RUNTIME_SA` can access every pinned secret version. |
| `/ready` returns `503` | Check that all three `STAMPBOT_*` secret bindings exist. |
| GitHub gets `401` | The App and Secret Manager webhook-secret values differ. |
| The workflow deploys but configuration stays unchanged | This is expected; the workflow preserves the existing service configuration. |

The [operations runbook](operations.md) covers webhook and approval failures
after the service is healthy.

## Remove the deployment

> **These commands delete the service, identities, federation pool, and
> credentials.** Confirm that no other workload uses them before you continue.

```bash
gcloud run services delete "${SERVICE_NAME}" --region="${REGION}"
gcloud iam workload-identity-pools delete github-pool --location=global
gcloud iam service-accounts delete "${DEPLOYER_SA}"
gcloud iam service-accounts delete "${RUNTIME_SA}"
gcloud secrets delete stampbot-app-id
gcloud secrets delete stampbot-private-key
gcloud secrets delete stampbot-webhook-secret
```
