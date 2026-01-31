# Stampbot Installation Guide

This guide covers how to set up and deploy Stampbot in various environments.

## Table of Contents

- [Creating a GitHub App](#creating-a-github-app)
- [Local Development](#local-development)
- [Docker Deployment](#docker-deployment)
- [Kubernetes Deployment](#kubernetes-deployment)
  - [Basic Helm Installation](#basic-helm-installation)
  - [EKS with AWS Secrets Manager](#eks-with-aws-secrets-manager)
  - [With External Secrets Operator](#with-external-secrets-operator)
  - [With VPA and Custom Metrics HPA](#with-vpa-and-custom-metrics-hpa)
- [Configuration](#configuration)

## Creating a GitHub App

### Option 1: Automated Setup (Recommended)

Stampbot includes a built-in setup wizard that creates your GitHub App automatically with the correct permissions.

1. **Start stampbot without credentials**
   ```bash
   make dev
   ```

2. **Open the setup page**
   - Visit http://localhost:8000 (or your deployed URL)
   - You'll be automatically redirected to `/setup`

3. **Create your GitHub App**
   - Click "Create GitHub App"
   - GitHub will show the app creation page with pre-configured permissions
   - Complete the creation process

4. **Enter your webhook URL**
   - GitHub will prompt you to enter the webhook URL during app creation
   - Use your public URL with `/webhook` path (e.g., `https://your-domain.com/webhook`)
   - For local development with ngrok, use your ngrok URL

5. **Save credentials**
   - After creation, you'll be redirected back to stampbot
   - Copy the displayed credentials to your `.env` file or Kubernetes secrets

6. **Restart stampbot**
   ```bash
   make dev
   ```

7. **Install the app**
   - Follow the link on the completion page to install the app on your repositories

### Option 2: Manual Setup

If you prefer to create the GitHub App manually:

1. **Navigate to GitHub App Settings**
   - Go to your organization or user settings
   - Click "Developer settings" → "GitHub Apps" → "New GitHub App"

2. **Configure Basic Information**
   - **Name**: `stampbot` (or your preferred name)
   - **Homepage URL**: Your application URL
   - **Webhook URL**: `https://your-domain.com/webhook`
   - **Webhook secret**: Generate a secure random string

3. **Set Permissions**

   Repository permissions:
   - **Pull requests**: Read & write
   - **Contents**: Read-only (to read stampbot.toml)
   - **Metadata**: Read-only

4. **Subscribe to Events**
   - Pull request
   - Pull request review comment
   - Issue comment

5. **Create the App**
   - Click "Create GitHub App"
   - Note your **App ID**
   - Generate and download a **private key**

6. **Install the App**
   - Go to "Install App" in the left sidebar
   - Install on your organization or repositories

## Local Development

### Prerequisites

- Python 3.11 or higher
- pip or poetry

### Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/dannysauer/stampbot.git
   cd stampbot
   ```

2. **Install dependencies**
   ```bash
   # Using make
   make install-dev

   # Or using pip directly
   pip install -r requirements.txt
   ```

3. **Run stampbot and complete setup**
   ```bash
   make dev
   ```

   Open http://localhost:8000 and follow the setup wizard to create your GitHub App.
   After setup, save the credentials to your `.env` file and restart.

4. **Set up ngrok for webhook testing** (optional)

   For local development, you'll need a public URL for GitHub webhooks:
   ```bash
   ngrok http 8000
   ```

   During GitHub App setup, enter your ngrok URL with `/webhook` path
   (e.g., `https://abc123.ngrok.io/webhook`). You can update this later
   in your GitHub App's settings if the URL changes.

5. **Test the application**
   ```bash
   curl http://localhost:8000/health
   ```

### Manual Configuration (Alternative)

Stampbot uses Dynaconf for configuration. In order of precedence it reads:
environment variables (`STAMPBOT_*`), `.secrets.toml`, `settings.toml`, and `.env`
(use `.env` only for local development).

If you already have credentials from the automated setup or manual GitHub App creation,
set the environment variables (or create a local `.env` file for dev):

Example `.env`:
```env
STAMPBOT_APP_ID=123456
STAMPBOT_PRIVATE_KEY=/path/to/your-private-key.pem
STAMPBOT_WEBHOOK_SECRET=your-webhook-secret
STAMPBOT_LOG_LEVEL=DEBUG
```

Run the application:
```bash
make dev
```

## Docker Deployment

### Build the Image

```bash
# Using make
make docker-build

# Or using docker directly
docker build -t stampbot:latest .
```

### Run the Container

```bash
docker run -d \
  --name stampbot \
  -p 8000:8000 \
  -e STAMPBOT_APP_ID=123456 \
  -e STAMPBOT_PRIVATE_KEY="$(cat private-key.pem)" \
  -e STAMPBOT_WEBHOOK_SECRET=your-webhook-secret \
  stampbot:latest
```

### Using Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  stampbot:
    build: .
    ports:
      - "8000:8000"
    environment:
      STAMPBOT_APP_ID: ${STAMPBOT_APP_ID}
      STAMPBOT_PRIVATE_KEY: ${STAMPBOT_PRIVATE_KEY}
      STAMPBOT_WEBHOOK_SECRET: ${STAMPBOT_WEBHOOK_SECRET}
      STAMPBOT_LOG_LEVEL: INFO
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Run with:
```bash
docker-compose up -d
```

## Kubernetes Deployment

### Installing from OCI Registry

The Stampbot Helm chart is published to GitHub Container Registry as an OCI artifact.

```bash
# Add the OCI registry (one-time setup)
# Note: OCI registries don't require 'helm repo add', you reference them directly

# Install the latest chart version
helm install stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --namespace stampbot \
  --create-namespace \
  --set github.existingSecret=stampbot-github

# Install a specific version
helm install stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --version 1.0.0 \
  --namespace stampbot \
  --create-namespace \
  --set github.existingSecret=stampbot-github

# Upgrade to latest
helm upgrade stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --namespace stampbot \
  --reuse-values

# Pull chart locally for inspection
helm pull oci://ghcr.io/dannysauer/charts/stampbot --untar
```

### Basic Helm Installation

1. **Create a namespace**
   ```bash
   kubectl create namespace stampbot
   ```

2. **Create secrets**
   ```bash
   kubectl create secret generic stampbot-github \
     --from-literal=STAMPBOT_APP_ID=123456 \
     --from-file=STAMPBOT_PRIVATE_KEY=./private-key.pem \
     --from-literal=STAMPBOT_WEBHOOK_SECRET=your-webhook-secret \
     -n stampbot
   ```

3. **Install with Helm**
   ```bash
   # From OCI registry (recommended)
   helm install stampbot oci://ghcr.io/dannysauer/charts/stampbot \
     --namespace stampbot \
     --set github.existingSecret=stampbot-github

   # Or from local chart (for development)
   helm install stampbot charts/stampbot \
     --namespace stampbot \
     --set github.existingSecret=stampbot-github
   ```

4. **Verify deployment**
   ```bash
   kubectl get pods -n stampbot
   kubectl logs -f deployment/stampbot -n stampbot
   ```

### EKS with AWS Secrets Manager

This setup uses IRSA (IAM Roles for Service Accounts) and External Secrets Operator.

#### Prerequisites

- External Secrets Operator installed in your cluster
- AWS Secrets Manager secret created

#### Steps

1. **Create AWS Secrets Manager secret**
   ```bash
   aws secretsmanager create-secret \
     --name stampbot/github-credentials \
     --secret-string '{
       "app_id": "123456",
       "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
       "webhook_secret": "your-webhook-secret"
     }' \
     --region us-east-1
   ```

2. **Create IAM policy**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "secretsmanager:GetSecretValue",
           "secretsmanager:DescribeSecret"
         ],
         "Resource": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:stampbot/github-credentials*"
       }
     ]
   }
   ```

3. **Create IAM role with IRSA**
   ```bash
   eksctl create iamserviceaccount \
     --name stampbot \
     --namespace stampbot \
     --cluster your-cluster-name \
     --attach-policy-arn arn:aws:iam::ACCOUNT_ID:policy/stampbot-secrets-policy \
     --approve
   ```

4. **Install External Secrets Operator** (if not already installed)
   ```bash
   helm repo add external-secrets https://charts.external-secrets.io
   helm install external-secrets \
     external-secrets/external-secrets \
     -n external-secrets-system \
     --create-namespace
   ```

5. **Create SecretStore**
   ```yaml
   apiVersion: external-secrets.io/v1beta1
   kind: SecretStore
   metadata:
     name: aws-secrets-manager
     namespace: stampbot
   spec:
     provider:
       aws:
         service: SecretsManager
         region: us-east-1
         auth:
           jwt:
             serviceAccountRef:
               name: stampbot
   ```

6. **Install Stampbot with External Secrets**
   ```bash
   helm install stampbot charts/stampbot \
     --namespace stampbot \
     --set externalSecrets.enabled=true \
     --set externalSecrets.secretStore.name=aws-secrets-manager \
     --set awsSecretsManager.enabled=true \
     --set awsSecretsManager.secretName=stampbot/github-credentials \
     --set awsSecretsManager.region=us-east-1 \
     --set image.repository=ghcr.io/dannysauer/stampbot \
     --set image.tag=latest
   ```

### With External Secrets Operator

If you're not using AWS but have External Secrets Operator:

```bash
helm install stampbot charts/stampbot \
  --namespace stampbot \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name=your-secret-store \
  --set externalSecrets.secretStore.kind=SecretStore \
  --set image.repository=ghcr.io/dannysauer/stampbot \
  --set image.tag=latest
```

### With VPA and Custom Metrics HPA

#### Prerequisites

- Vertical Pod Autoscaler installed
- Prometheus Adapter or custom metrics API server

#### Install VPA

```bash
git clone https://github.com/kubernetes/autoscaler.git
cd autoscaler/vertical-pod-autoscaler
./hack/vpa-up.sh
```

#### Install Prometheus Adapter

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  -n monitoring \
  --create-namespace
```

#### Deploy Stampbot with VPA and Custom Metrics

Create `values-production.yaml`:

```yaml
replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 20
  targetCPUUtilizationPercentage: 70
  customMetrics:
    enabled: true
    metrics:
      - type: Pods
        pods:
          metric:
            name: stampbot_http_requests_total
          target:
            type: AverageValue
            averageValue: "100"

vpa:
  enabled: true
  updateMode: "Auto"
  minAllowed:
    cpu: 100m
    memory: 128Mi
  maxAllowed:
    cpu: 2000m
    memory: 2Gi

resources:
  requests:
    cpu: 200m
    memory: 256Mi
  limits:
    cpu: 1000m
    memory: 1Gi

podDisruptionBudget:
  enabled: true
  minAvailable: 2

metrics:
  enabled: true
  serviceMonitor:
    enabled: true
```

Install:

```bash
helm install stampbot charts/stampbot \
  --namespace stampbot \
  --values values-production.yaml \
  --set github.existingSecret=stampbot-github
```

## Configuration

### Ingress Setup

For production, configure an Ingress:

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: stampbot.yourdomain.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: stampbot-tls
      hosts:
        - stampbot.yourdomain.com
```

### OpenTelemetry

Enable distributed tracing:

```yaml
config:
  otelEnabled: true
  otelEndpoint: "http://otel-collector:4317"
```

### Network Policies

Enable network policies for security:

```yaml
networkPolicy:
  enabled: true
  ingress:
    - from:
      - namespaceSelector:
          matchLabels:
            name: ingress-nginx
  egress:
    - to:
      - namespaceSelector: {}
    - ports:
      - protocol: TCP
        port: 443
```

## Verification

1. **Check pod status**
   ```bash
   kubectl get pods -n stampbot
   ```

2. **Check logs**
   ```bash
   kubectl logs -f deployment/stampbot -n stampbot
   ```

3. **Test health endpoint**
   ```bash
   kubectl port-forward svc/stampbot 8000:80 -n stampbot
   curl http://localhost:8000/health
   ```

4. **Check metrics**
   ```bash
   kubectl port-forward svc/stampbot 8000:80 -n stampbot
   curl http://localhost:8000/metrics
   ```

## Troubleshooting

### Pod not starting

Check events:
```bash
kubectl describe pod -n stampbot
```

### Authentication errors

Verify secrets:
```bash
kubectl get secret stampbot-github -n stampbot -o yaml
```

### Webhook not receiving events

1. Check GitHub App webhook settings
2. Verify ingress is working
3. Check webhook secret matches

### High memory usage

Enable VPA:
```bash
helm upgrade stampbot charts/stampbot \
  --set vpa.enabled=true \
  --reuse-values
```

## Next Steps

- Configure `stampbot.toml` in your repositories
- Set up monitoring and alerting
- Review logs and metrics
- Test approval workflows
