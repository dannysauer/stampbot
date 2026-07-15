# Deploy Stampbot to Google Cloud Run

Use this guide to create a Cloud Run service and connect it to Stampbot's
`Deploy to Cloud Run` workflow.

The workflow deploys a versioned Docker Hub image to an existing service. It
also sets port `8000` and allows unauthenticated requests. It doesn't supply a
runtime identity, environment variables, or Secret Manager bindings, so Cloud
Run keeps those parts of the service configuration.

This setup uses two Google service accounts. GitHub Actions impersonates the
deployer account. The Stampbot container runs as the runtime account.

## Before you begin

Run the commands in Bash. You need:

- a Google Cloud project with billing enabled;
- `gcloud` authenticated as an administrator for that project;
- [GitHub CLI](https://cli.github.com/) 2.93.0 or newer, authenticated as a
  repository administrator;
- [crane](https://github.com/google/go-containerregistry/tree/main/cmd/crane)
  to resolve the image tag before deployment;
- an existing GitHub App ID, private key, and webhook secret; and
- a published Stampbot version that you have verified with
  [Verify a Stampbot release](release-verification.md).

Set names for the resources. Replace `REPLACE_WITH_PROJECT_ID`,
`OWNER/REPOSITORY`, and `REPLACE_WITH_VERIFIED_APP_VERSION`. The other values
are safe defaults for a dedicated deployment:

```bash
set -euo pipefail

PROJECT_ID=REPLACE_WITH_PROJECT_ID
REGION=us-central1
SERVICE_NAME=stampbot
REPOSITORY=OWNER/REPOSITORY
APP_VERSION=REPLACE_WITH_VERIFIED_APP_VERSION
VERIFIED_IMAGE=ghcr.io/dannysauer/stampbot
DEPLOY_IMAGE=docker.io/stampbot/stampbot
POOL_ID=stampbot-github
PROVIDER_ID=stampbot-main
DEPLOYER_NAME=github-actions-deployer
RUNTIME_NAME=stampbot-runtime

if [[ "${PROJECT_ID}" == REPLACE_WITH_PROJECT_ID || \
      "${REPOSITORY}" == OWNER/REPOSITORY || \
      "${APP_VERSION}" == REPLACE_WITH_VERIFIED_APP_VERSION ]]; then
  printf 'Replace PROJECT_ID, REPOSITORY, and APP_VERSION before continuing.\n' >&2
  exit 1
fi

gh auth status
gcloud config set project "${PROJECT_ID}"

PROJECT_NUMBER="$(
  gcloud projects describe "${PROJECT_ID}" \
    --format='value(projectNumber)'
)"
REPOSITORY_ID="$(gh api "repos/${REPOSITORY}" --jq '.id')"
DEPLOYER_SA="${DEPLOYER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
OPERATOR_EMAIL="$(gcloud config get-value account)"
```

Use a release whose files and image attestations passed the release verification
guide. The GitHub Container Registry (GHCR) coordinate above is Stampbot's
current distribution location. Historical releases keep the workflow identity
and registry recorded when they were built.

Keep Google publishing disabled while you create and test the deployment:

```bash
gh variable set GOOGLE_PUBLISHING_ENABLED \
  --repo "${REPOSITORY}" \
  --body 0

test "$(
  gh variable get GOOGLE_PUBLISHING_ENABLED \
    --repo "${REPOSITORY}" \
    --json value \
    --jq '.value'
)" = 0
```

The gate is fail-closed. Only the exact string `1` enables Cloud Run publishing.
An unset value, `0`, `true`, or any other value leaves it disabled.

## Enable the Google APIs

Enable the APIs used by Cloud Run, Workload Identity Federation, Google
Identity and Access Management (IAM), and Secret Manager:

```bash
gcloud services enable \
  run.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  secretmanager.googleapis.com
```

## Create the service accounts

Create separate deployment and runtime identities:

```bash
gcloud iam service-accounts create "${DEPLOYER_NAME}" \
  --display-name="GitHub Actions Cloud Run deployer"

gcloud iam service-accounts create "${RUNTIME_NAME}" \
  --display-name="Stampbot Cloud Run runtime"
```

Grant the deployer Cloud Run administration in this project:

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

The administrator who creates the first revision needs the same access:

```bash
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="user:${OPERATOR_EMAIL}" \
  --role=roles/iam.serviceAccountUser
```

## Federate GitHub Actions

Create a Workload Identity pool:

```bash
gcloud iam workload-identity-pools create "${POOL_ID}" \
  --location=global \
  --display-name="Stampbot GitHub Actions"
```

Create a provider that accepts only this repository's numeric ID and only the
`main` branch:

```bash
gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" \
  --display-name="Stampbot main branch" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id" \
  --attribute-condition="assertion.repository_id == '${REPOSITORY_ID}' && assertion.ref == 'refs/heads/main'" \
  --issuer-uri=https://token.actions.githubusercontent.com
```

The numeric repository ID survives a rename or transfer and can't be claimed by
another repository. A condition based only on `assertion.repository` would need
an update after either change.

Allow that repository to impersonate the deployer:

```bash
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository_id/${REPOSITORY_ID}"
```

This binding doesn't grant access to every identity in the pool. The mapped
repository ID selects the only principal set that can use the deployer.

## Configure GitHub Actions

Store the provider resource name and deployer identity as repository secrets.
The commands pipe both values directly to `gh`; they don't place either value on
the command line:

```bash
set +x

gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" \
  --format='value(name)' | \
  gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "${REPOSITORY}"

printf '%s' "${DEPLOYER_SA}" | \
  gh secret set GCP_SERVICE_ACCOUNT --repo "${REPOSITORY}"
```

Treat these values as sensitive operational identifiers. Don't put them in
issues, pull requests, workflow logs, or public documentation. Don't run these
commands with shell tracing enabled.

Set the non-secret deployment coordinates:

```bash
gh variable set CLOUDRUN_REGION \
  --repo "${REPOSITORY}" \
  --body "${REGION}"

gh variable set CLOUDRUN_SERVICE_NAME \
  --repo "${REPOSITORY}" \
  --body "${SERVICE_NAME}"
```

`CLOUDRUN_REGION` defaults to `us-central1` when it is absent.
`CLOUDRUN_SERVICE_NAME` defaults to `stampbot`.

If the repository moves, set `GOOGLE_PUBLISHING_ENABLED` to `0` before the
transfer. The numeric repository condition above should remain valid, but
confirm the repository secrets and variables after the move. Run the disabled
workflow test again before you set the gate to `1`. GitHub redirects don't
repair a name-based federation condition.

## Store the GitHub App credentials

Create one Secret Manager secret for each credential. The prompts keep the App
ID and webhook secret out of shell history:

```bash
read -r -p 'Path to the GitHub App private key: ' PRIVATE_KEY_FILE
test -r "${PRIVATE_KEY_FILE}"

read -r -p 'GitHub App ID: ' STAMPBOT_APP_ID
printf '%s' "${STAMPBOT_APP_ID}" | \
  gcloud secrets create stampbot-app-id --data-file=-
unset STAMPBOT_APP_ID

gcloud secrets create stampbot-private-key \
  --data-file="${PRIVATE_KEY_FILE}"
unset PRIVATE_KEY_FILE

read -r -s -p 'GitHub webhook secret: ' STAMPBOT_WEBHOOK_SECRET
printf '\n'
printf '%s' "${STAMPBOT_WEBHOOK_SECRET}" | \
  gcloud secrets create stampbot-webhook-secret --data-file=-
unset STAMPBOT_WEBHOOK_SECRET
```

Each new secret begins with version `1`. Let only the runtime identity read
them:

```bash
for SECRET in stampbot-app-id stampbot-private-key stampbot-webhook-secret; do
  gcloud secrets add-iam-policy-binding "${SECRET}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor
done
```

Cloud Run resolves secret environment variables when an instance starts. Pin a
numeric version instead of `latest`; the
[Cloud Run secret guide](https://cloud.google.com/run/docs/configuring/services/secrets)
explains why mounted secrets behave differently.

## Create the service

> **Warning:** GitHub must reach `/webhook` without Cloud Run authentication.
> `--allow-unauthenticated` exposes the whole service, not one path. That
> includes `/health`, `/ready`, `/docs`, `/redoc`, and `/openapi.json`;
> `/metrics` isn't registered on this listener and returns `404`. If that
> surface is unacceptable, put a path-aware gateway in front of Stampbot and
> restrict service ingress so clients can't bypass it.

The checked-in workflow deploys a version tag, not a digest. Resolve the
verified GHCR image and Docker Hub mirror now. The two registry indexes must be
identical before you continue:

```bash
EXPECTED_INDEX_DIGEST="$(
  crane digest "${VERIFIED_IMAGE}:${APP_VERSION}"
)"
MIRROR_INDEX_DIGEST="$(
  crane digest "${DEPLOY_IMAGE}:${APP_VERSION}"
)"
test "${MIRROR_INDEX_DIGEST}" = "${EXPECTED_INDEX_DIGEST}"

EXPECTED_RUNTIME_DIGEST="$(
  crane digest \
    --platform linux/amd64 \
    "${DEPLOY_IMAGE}@${EXPECTED_INDEX_DIGEST}"
)"

printf 'Approved image index: %s@%s\n' \
  "${DEPLOY_IMAGE}" "${EXPECTED_INDEX_DIGEST}"
printf 'Approved Cloud Run image: %s@%s\n' \
  "${DEPLOY_IMAGE}" "${EXPECTED_RUNTIME_DIGEST}"
```

Cloud Run runs the `linux/amd64` image selected from Stampbot's multi-platform
index. A revision keeps the digest it resolved at creation, but a later
workflow rerun can resolve a changed tag to different bytes.

Create the first revision from the approved index digest. Setup stays closed
because this guide starts with an existing GitHub App:

```bash
gcloud run deploy "${SERVICE_NAME}" \
  --region="${REGION}" \
  --image="${DEPLOY_IMAGE}@${EXPECTED_INDEX_DIGEST}" \
  --service-account="${RUNTIME_SA}" \
  --port=8000 \
  --allow-unauthenticated \
  --set-env-vars="STAMPBOT_SETUP_ENABLED=false,STAMPBOT_SETUP_ALLOW_CONFIGURED=false,STAMPBOT_LOG_FORMAT=json" \
  --update-secrets="STAMPBOT_APP_ID=stampbot-app-id:1,STAMPBOT_PRIVATE_KEY=stampbot-private-key:1,STAMPBOT_WEBHOOK_SECRET=stampbot-webhook-secret:1" # pragma: allowlist secret
```

GitHub webhooks require an unauthenticated route. That also makes the app's
other routes public unless you put a path-aware proxy in front of Cloud Run.
Stampbot doesn't register `/metrics` on this listener. Leave
`STAMPBOT_METRICS_ENABLED=false`. Cloud Run routes only the configured container
port, so its service URL can't scrape Stampbot's separate metrics listener.

Read the service URL and set it as Stampbot's public origin:

```bash
SERVICE_URL="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --format='value(status.url)'
)"
printf 'Stampbot URL: %s\n' "${SERVICE_URL}"
```

Set the GitHub App's **Webhook URL** to `${SERVICE_URL}/webhook`. If you add a
custom domain later, update the App to the custom `/webhook` URL.

`STAMPBOT_BASE_URL` is required only when the first-run setup flow is enabled.
If you deliberately enable setup later, set it to the trusted public URL before
you expose `/setup`. Stampbot never derives setup URLs from request headers.

## Verify the first revision

Confirm that Cloud Run resolved the expected digest:

```bash
LATEST_REVISION="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --format='value(status.latestReadyRevisionName)'
)"
DEPLOYED_DIGEST="$(
  gcloud run revisions describe "${LATEST_REVISION}" \
    --region="${REGION}" \
    --format='value(status.imageDigest)'
)"
case "${DEPLOYED_DIGEST}" in
  "${EXPECTED_INDEX_DIGEST}"|"${EXPECTED_RUNTIME_DIGEST}") ;;
  *)
    printf 'Unexpected deployed digest: %s\n' "${DEPLOYED_DIGEST}" >&2
    exit 1
    ;;
esac
```

Check liveness and readiness:

```bash
curl -fsS "${SERVICE_URL}/health"
curl -fsS "${SERVICE_URL}/ready"
```

A configured service returns this readiness state:

```json
{
  "status": "ready",
  "checks": {
    "configured": true,
    "setup_enabled": false
  }
}
```

Confirm that the setup routes are closed:

```bash
for PATH_SUFFIX in /setup '/setup/callback?code=unused' /setup/status; do
  test "$(
    curl -sS -o /dev/null -w '%{http_code}' \
      "${SERVICE_URL}${PATH_SUFFIX}"
  )" = 403
done
```

Read recent logs without copying them into a public issue:

```bash
gcloud run services logs read "${SERVICE_NAME}" \
  --region="${REGION}" \
  --limit=50
```

Use the GitHub App settings to redeliver a `ping` webhook. A healthy delivery
returns:

```json
{"status":"ok","message":"pong"}
```

## Test the disabled workflow

Keep `GOOGLE_PUBLISHING_ENABLED` at `0`. Dispatch the workflow from `main` and
capture the run ID:

```bash
RUN_URL="$(
  gh workflow run deploy-cloudrun.yml \
    --repo "${REPOSITORY}" \
    --ref main \
    --raw-field image_tag="${APP_VERSION}"
)"
test -n "${RUN_URL}"
RUN_ID="${RUN_URL##*/}"

gh run watch "${RUN_ID}" --repo "${REPOSITORY}"

test "$(
  gh run view "${RUN_ID}" \
    --repo "${REPOSITORY}" \
    --json jobs \
    --jq '[.jobs[] | select(.name == "Deploy to Cloud Run")][0].conclusion'
)" = skipped
```

The entire deployment job should be skipped. No Google authentication or
deployment step should run.

## Enable the workflow

> **Warning:** Setting the gate to `1` permits both manual deployments and
> automatic deployment during an app release. Cloud Run may incur charges.
> Continue only after billing, federation, secrets, and the initial service
> configuration have been approved.

Enable the gate and read it back:

```bash
gh variable set GOOGLE_PUBLISHING_ENABLED \
  --repo "${REPOSITORY}" \
  --body 1

test "$(
  gh variable get GOOGLE_PUBLISHING_ENABLED \
    --repo "${REPOSITORY}" \
    --json value \
    --jq '.value'
)" = 1
```

Deploy the inspected version from `main`:

```bash
RUN_URL="$(
  gh workflow run deploy-cloudrun.yml \
    --repo "${REPOSITORY}" \
    --ref main \
    --raw-field image_tag="${APP_VERSION}"
)"
test -n "${RUN_URL}"
RUN_ID="${RUN_URL##*/}"

gh run watch "${RUN_ID}" \
  --repo "${REPOSITORY}" \
  --exit-status
```

The workflow authenticates through federation and deploys
`docker.io/stampbot/stampbot:${APP_VERSION}`. It doesn't rewrite the service's
runtime identity, secret bindings, or environment variables.

Resolve the Docker Hub tag again. It must still match the approved GHCR index.
Then compare the new revision with the approved index or its `linux/amd64`
image:

```bash
CURRENT_INDEX_DIGEST="$(
  crane digest "${DEPLOY_IMAGE}:${APP_VERSION}"
)"
test "${CURRENT_INDEX_DIGEST}" = "${EXPECTED_INDEX_DIGEST}"

LATEST_REVISION="$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --format='value(status.latestReadyRevisionName)'
)"
DEPLOYED_DIGEST="$(
  gcloud run revisions describe "${LATEST_REVISION}" \
    --region="${REGION}" \
    --format='value(status.imageDigest)'
)"
case "${DEPLOYED_DIGEST}" in
  "${EXPECTED_INDEX_DIGEST}"|"${EXPECTED_RUNTIME_DIGEST}") ;;
  *)
    printf 'Unexpected deployed digest: %s\n' "${DEPLOYED_DIGEST}" >&2
    exit 1
    ;;
esac

curl -fsS "${SERVICE_URL}/ready"
```

To stop both release and manual deployment, set the gate back to `0`:

```bash
gh variable set GOOGLE_PUBLISHING_ENABLED \
  --repo "${REPOSITORY}" \
  --body 0
```

## Rotate a secret

Add a Secret Manager version instead of replacing the secret. This example
rotates the webhook secret without putting it in shell history:

```bash
read -r -s -p 'New GitHub webhook secret: ' NEW_WEBHOOK_SECRET
printf '\n'

NEW_VERSION="$(
  printf '%s' "${NEW_WEBHOOK_SECRET}" | \
    gcloud secrets versions add stampbot-webhook-secret \
      --data-file=- \
      --format='value(name.basename())'
)"

gcloud run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --update-secrets="STAMPBOT_WEBHOOK_SECRET=stampbot-webhook-secret:${NEW_VERSION}" # pragma: allowlist secret
```

Update the GitHub App to the same webhook secret, then clear the shell value:

```bash
unset NEW_WEBHOOK_SECRET
```

There is no atomic update across GitHub and Google Cloud. Plan for a short
window in which one side may reject webhook signatures.

Wait for the new revision, check `/ready`, and redeliver a recent event. After
that succeeds, list the versions and disable the old numeric version according
to your retention policy:

```bash
gcloud secrets versions list stampbot-webhook-secret

OLD_VERSION=REPLACE_WITH_OLD_NUMERIC_VERSION
gcloud secrets versions disable "${OLD_VERSION}" \
  --secret=stampbot-webhook-secret
```

## Roll back a revision

List the revisions before changing traffic:

```bash
gcloud run revisions list \
  --service="${SERVICE_NAME}" \
  --region="${REGION}"
```

Replace `PREVIOUS_REVISION` with the last healthy revision, then route all
traffic to it:

```bash
gcloud run services update-traffic "${SERVICE_NAME}" \
  --region="${REGION}" \
  --to-revisions="PREVIOUS_REVISION=100"

curl -fsS "${SERVICE_URL}/ready"
```

Traffic rollback doesn't restore a GitHub App setting or a Secret Manager
version. Reverse those changes separately when they caused the failure.

## Troubleshoot the deployment

| Symptom | Check |
| --- | --- |
| Federation fails | Confirm the provider uses the numeric Google project number, the GitHub repository ID matches, and the run uses `refs/heads/main`. |
| A new identity can't authenticate immediately | Wait a few minutes for new IAM and Workload Identity bindings to propagate, then retry. |
| Deployment can't act as the runtime account | Confirm the deployer has `roles/iam.serviceAccountUser` on `RUNTIME_SA`. |
| A revision won't start | Confirm `RUNTIME_SA` can access every pinned secret version. |
| `/ready` returns `503` | Confirm all three `STAMPBOT_*` secret bindings exist and contain usable values. |
| GitHub receives `401` | Confirm the GitHub App and Secret Manager contain the same webhook secret. |
| The workflow deploys but service configuration stays unchanged | This is expected; the workflow changes the image and reasserts its port and public access. |
| The deployed digest differs from the tag | Stop promotion. Resolve the registry tag again and investigate before rerunning the workflow. |

The [operations runbook](operations.md) covers webhook and approval failures
after the service is healthy.

## Remove the deployment

> **Warning:** The following commands delete the service, identities,
> federation pool, and credentials. Confirm that no other workload uses them.

Disable publishing first and remove the project-level deployer grant before
deleting the identity:

```bash
gh variable set GOOGLE_PUBLISHING_ENABLED \
  --repo "${REPOSITORY}" \
  --body 0

gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role=roles/run.admin

gcloud run services delete "${SERVICE_NAME}" --region="${REGION}"
gcloud iam workload-identity-pools delete "${POOL_ID}" --location=global
gcloud iam service-accounts delete "${DEPLOYER_SA}"
gcloud iam service-accounts delete "${RUNTIME_SA}"
gcloud secrets delete stampbot-app-id
gcloud secrets delete stampbot-private-key
gcloud secrets delete stampbot-webhook-secret

gh secret delete GCP_WORKLOAD_IDENTITY_PROVIDER --repo "${REPOSITORY}"
gh secret delete GCP_SERVICE_ACCOUNT --repo "${REPOSITORY}"
gh variable delete CLOUDRUN_REGION --repo "${REPOSITORY}"
gh variable delete CLOUDRUN_SERVICE_NAME --repo "${REPOSITORY}"
gh variable delete GOOGLE_PUBLISHING_ENABLED --repo "${REPOSITORY}"
```

An absent gate remains fail-closed. These commands don't delete the Google
Cloud project, disable its APIs, or close its billing account.
