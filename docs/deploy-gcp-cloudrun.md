# Deploying StampBot to Google Cloud Run

This guide covers deploying StampBot to Google Cloud Run using the automated GitHub Actions workflow.

## Prerequisites

- A Google Cloud account with billing enabled (free trial works)
- The `gcloud` CLI installed and authenticated
- Repository admin access to configure variables

## GCP Setup

### 1. Create a Project (if needed)

```bash
gcloud projects create YOUR_PROJECT_ID --name="StampBot"
gcloud config set project YOUR_PROJECT_ID
```

### 2. Enable Required APIs

```bash
gcloud services enable run.googleapis.com iamcredentials.googleapis.com
```

### 3. Create a Service Account

```bash
gcloud iam service-accounts create github-actions-deployer \
  --display-name="GitHub Actions Cloud Run Deployer"

# Grant Cloud Run Admin
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.admin"

# Grant Service Account User (to act as the runtime service account)
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

### 4. Set Up Workload Identity Federation

This allows GitHub Actions to authenticate to GCP without storing long-lived credentials.

```bash
# Create Workload Identity Pool
gcloud iam workload-identity-pools create github-pool \
  --location="global" \
  --display-name="GitHub Actions Pool"

# Create OIDC Provider (replace OWNER/REPO with your GitHub repository)
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == 'OWNER/REPO'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Get your project number
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')

# Allow GitHub repo to impersonate service account
gcloud iam service-accounts add-iam-policy-binding \
  github-actions-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/OWNER/REPO"
```

### 5. Configure GitHub Repository Variables

Go to your repository's Settings > Secrets and variables > Actions > Variables tab.

| Variable | Required | Description |
|----------|----------|-------------|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Yes | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | Yes | `github-actions-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com` |
| `CLOUDRUN_REGION` | No | GCP region (default: `us-central1`) |
| `CLOUDRUN_SERVICE_NAME` | No | Service name (default: `stampbot`) |

## Deploying

The workflow automatically deploys when a new release is published. You can also trigger a manual deployment:

1. Go to Actions > "Deploy to Cloud Run"
2. Click "Run workflow"
3. Optionally specify an image tag (defaults to latest release)

## Configuring StampBot

After the first deployment, configure StampBot's environment variables in Cloud Run:

```bash
gcloud run services update stampbot \
  --region=us-central1 \
  --set-env-vars="GITHUB_APP_ID=your-app-id" \
  --set-env-vars="GITHUB_WEBHOOK_SECRET=your-webhook-secret" \
  --set-env-vars="GITHUB_PRIVATE_KEY=your-private-key"
```

Or use the [Cloud Console](https://console.cloud.google.com/run) to configure environment variables and secrets.

## Cost Management

Cloud Run has a generous free tier:
- 2 million requests/month
- 360,000 GB-seconds of memory
- 180,000 vCPU-seconds

StampBot only runs when GitHub sends webhooks, so usage is minimal. To limit scaling:

```bash
gcloud run services update stampbot --max-instances=1 --region=us-central1
```

## Cleanup

To completely remove StampBot from Cloud Run:

```bash
gcloud run services delete stampbot --region=us-central1
```

To remove all GCP resources created for this deployment:

```bash
# Delete the Cloud Run service
gcloud run services delete stampbot --region=us-central1

# Delete the service account
gcloud iam service-accounts delete github-actions-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com

# Delete the Workload Identity Pool (this also deletes the provider)
gcloud iam workload-identity-pools delete github-pool --location=global
```
