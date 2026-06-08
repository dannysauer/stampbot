# Stampbot Installation Guide

This guide explains how to create the GitHub App and run Stampbot locally, with Docker,
or on Kubernetes. For exact configuration keys and failure behavior, see
[docs/configuration.md](docs/configuration.md).

## GitHub App Setup

Stampbot must run as a GitHub App. You can create the app with the built-in setup wizard
or manually in GitHub.

### Automated Setup

1. Start Stampbot without GitHub App credentials:

   ```bash
   make install-dev
   make dev
   ```

2. Open <http://localhost:8000>. Stampbot redirects to `/setup` when credentials are
   missing and setup mode is enabled.

3. Click **Create GitHub App** and complete the GitHub manifest flow.

4. Save the returned credentials as environment variables or deployment secrets:

   ```env
   STAMPBOT_APP_ID=123456
   STAMPBOT_WEBHOOK_SECRET=replace-with-webhook-secret
   STAMPBOT_PRIVATE_KEY="[PEM private key content with newlines escaped]"
   ```

5. Restart Stampbot and install the GitHub App on the repositories you want to automate.

For deployed setup behind a proxy or Cloud Run, set `STAMPBOT_BASE_URL` to the public
HTTPS origin before opening `/setup`.

### Manual Setup

Create a GitHub App under the target user or organization:

| GitHub App setting | Value |
| --- | --- |
| Homepage URL | Public Stampbot base URL. |
| Webhook URL | Public Stampbot base URL plus `/webhook`. |
| Webhook secret | Random secret that also becomes `STAMPBOT_WEBHOOK_SECRET`. |
| Public app | Disabled unless you intentionally operate a public app. |

Repository permissions:

| Permission | Level | Required for |
| --- | --- | --- |
| Pull requests | Read and write | Read PR state, create approval reviews, dismiss Stampbot approvals. |
| Contents | Read-only | Read `stampbot.toml` from target repos and org `.github` fallback. |
| Metadata | Read-only | Required baseline GitHub App repository metadata. |
| Issues | Read-only | Receive and inspect PR issue comments for ChatOps. |
| Members | Read-only | Check `allowed_teams` membership. |
| Administration | Read-only | Check collaborator permission for ChatOps authorization. |

Subscribe to these events:

- Pull request
- Issue comment
- Pull request review comment

After creating the app, record the App ID, generate a private key, and install the app on
the target repositories.

## Local Development

Prerequisites:

- Python 3.11 or newer.
- Poetry, or use the checked-in requirements file.

Install dependencies:

```bash
git clone https://github.com/dannysauer/stampbot.git
cd stampbot
make install-dev
```

Run the service:

```bash
make dev
```

Verify:

```bash
curl http://localhost:8000/health
```

For GitHub webhook testing from a local machine, expose port `8000` through a public HTTPS
tunnel and set the GitHub App webhook URL to `https://YOUR-TUNNEL/webhook`.

## Docker

Build:

```bash
make docker-build
```

Run with a local `.env` file:

```bash
docker run --rm \
  --name stampbot \
  -p 8000:8000 \
  --env-file .env \
  stampbot:latest
```

Required production variables:

```env
STAMPBOT_APP_ID=123456
STAMPBOT_PRIVATE_KEY=/run/secrets/stampbot-private-key.pem
STAMPBOT_WEBHOOK_SECRET=replace-with-webhook-secret
STAMPBOT_SETUP_ENABLED=false
```

## Kubernetes with Helm

The chart is documented as an installable package in
[charts/stampbot/README.md](charts/stampbot/README.md).

Create credentials:

```bash
kubectl create namespace stampbot
kubectl create secret generic stampbot-github \
  --namespace stampbot \
  --from-literal=STAMPBOT_APP_ID=123456 \
  --from-file=STAMPBOT_PRIVATE_KEY=./private-key.pem \
  --from-literal=STAMPBOT_WEBHOOK_SECRET=replace-with-webhook-secret
```

Install from the OCI registry:

```bash
helm install stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --namespace stampbot \
  --set github.existingSecret=stampbot-github
```

Install from a local checkout:

```bash
helm install stampbot charts/stampbot \
  --namespace stampbot \
  --set github.existingSecret=stampbot-github
```

Verify:

```bash
kubectl rollout status deployment/stampbot --namespace stampbot
kubectl logs deployment/stampbot --namespace stampbot --tail=100
kubectl port-forward svc/stampbot 8000:80 --namespace stampbot
curl http://127.0.0.1:8000/health
```

For production ingress:

```yaml
github:
  existingSecret: stampbot-github

setup:
  enabled: false
  baseUrl: https://stampbot.example.com

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: stampbot.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: stampbot-tls
      hosts:
        - stampbot.example.com
```

## EKS with External Secrets and IRSA

Prerequisites:

- External Secrets Operator installed.
- AWS Secrets Manager secret with GitHub App credential properties.
- IAM policy allowing `secretsmanager:GetSecretValue` and
  `secretsmanager:DescribeSecret` for that secret.

Example AWS secret value:

```json
{
  "app_id": "123456",
  "private_key": "[PEM private key content]",
  "webhook_secret": ""
}
```

Create a SecretStore that uses the Stampbot service account:

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

Use one service account ownership model.

### Pre-created Service Account

Create the service account with `eksctl`, then tell Helm to use it:

```bash
eksctl create iamserviceaccount \
  --cluster example-cluster \
  --namespace stampbot \
  --name stampbot \
  --attach-policy-arn arn:aws:iam::123456789012:policy/stampbot-secrets-policy \
  --approve

helm upgrade --install stampbot charts/stampbot \
  --namespace stampbot \
  --set serviceAccount.create=false \
  --set serviceAccount.name=stampbot \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name=aws-secrets-manager
```

### Helm-created Service Account

Let Helm create the service account and provide the role ARN:

```bash
helm upgrade --install stampbot charts/stampbot \
  --namespace stampbot \
  --set awsSecretsManager.enabled=true \
  --set awsSecretsManager.roleArn=arn:aws:iam::123456789012:role/stampbot-secrets \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name=aws-secrets-manager
```

When `awsSecretsManager.enabled=true`, `awsSecretsManager.roleArn` is required. Rendering
fails if the ARN is empty.

Verify External Secrets:

```bash
kubectl get serviceaccount stampbot --namespace stampbot -o yaml
kubectl get externalsecret --namespace stampbot
kubectl get secret stampbot-external --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
```

## Optional Autoscaling and Monitoring

Enable ServiceMonitor only when the Prometheus Operator CRDs are installed:

```yaml
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
```

Enable VPA only when the Vertical Pod Autoscaler CRDs are installed:

```yaml
vpa:
  enabled: true
  updateMode: Auto
```

Enable custom HPA metrics only when the custom metrics API is available:

```yaml
autoscaling:
  enabled: true
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
```

## Troubleshooting and Rollback

Use [docs/operations.md](docs/operations.md) for webhook delivery triage, GitHub App
permission problems, invalid repository config, metrics, rate limits, and rollback steps.

Common verification commands:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/setup/status
helm lint charts/stampbot
helm template stampbot charts/stampbot --set github.existingSecret=stampbot-github
```
