# Stampbot Helm Chart

This chart deploys Stampbot, a GitHub App that approves or dismisses its own pull
request reviews based on labels and ChatOps comments.

## Prerequisites

- Kubernetes 1.25 or newer.
- Helm 3.12 or newer.
- A GitHub App created from the Stampbot manifest or manual setup instructions.
- A public HTTPS route to `POST /webhook` for GitHub webhooks.
- A Kubernetes Secret, External Secrets Operator target, or inline values containing:
  `STAMPBOT_APP_ID`, `STAMPBOT_PRIVATE_KEY`, and `STAMPBOT_WEBHOOK_SECRET`.

Optional features require their CRDs or controllers before installation:

| Feature | Required dependency |
| --- | --- |
| `metrics.serviceMonitor.enabled` | Prometheus Operator `monitoring.coreos.com/v1` ServiceMonitor CRD |
| `vpa.enabled` | Vertical Pod Autoscaler `autoscaling.k8s.io/v1` CRD |
| `externalSecrets.enabled` | External Secrets Operator `external-secrets.io/v1beta1` CRD |
| `autoscaling.customMetrics.enabled` | Kubernetes custom metrics API, such as Prometheus Adapter |

## Install

Create the namespace and GitHub App Secret first:

```bash
kubectl create namespace stampbot
kubectl create secret generic stampbot-github \
  --namespace stampbot \
  --from-literal=STAMPBOT_APP_ID=123456 \
  --from-file=STAMPBOT_PRIVATE_KEY=./private-key.pem \
  --from-literal=STAMPBOT_WEBHOOK_SECRET=replace-with-random-secret
```

Install from the OCI chart registry:

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

For production, configure ingress and set `setup.enabled=false`. The chart default already
disables setup mode.

```yaml
github:
  existingSecret: stampbot-github

setup:
  enabled: false
  baseUrl: https://stampbot.example.com

ingress:
  enabled: true
  className: nginx
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

## Upgrade

```bash
helm upgrade stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --namespace stampbot \
  --reuse-values
```

Use immutable image tags or chart versions for controlled rollouts:

```bash
helm upgrade stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --namespace stampbot \
  --version 0.1.0 \
  --set image.tag=0.1.0 \
  --reuse-values
```

## Roll Back

```bash
helm history stampbot --namespace stampbot
helm rollback stampbot 1 --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
```

If a bad credential rotation caused the failure, restore the previous Secret value before
rolling back pods.

## Uninstall

```bash
helm uninstall stampbot --namespace stampbot
kubectl delete secret stampbot-github --namespace stampbot
```

The Secret removal command deletes manually created credentials. Do not run it if the same
Secret is shared by another release.

## Secret Contracts

The deployment reads all GitHub App credentials from environment variables. Secrets must
use these keys:

| Key | Required | Description |
| --- | --- | --- |
| `STAMPBOT_APP_ID` | Yes | Numeric GitHub App ID. |
| `STAMPBOT_PRIVATE_KEY` | Yes | PEM private key content or a path inside the container. For Kubernetes Secrets, use PEM content. |
| `STAMPBOT_WEBHOOK_SECRET` | Yes | Shared secret configured on the GitHub App webhook. |

Secret sourcing modes:

| Mode | Values | Behavior |
| --- | --- | --- |
| Existing Secret | `github.existingSecret` | Deployment reads the named Secret. The chart does not create credentials. |
| Inline values | `github.appId`, `github.privateKey`, `github.webhookSecret` | Chart creates an Opaque Secret. Use only for local testing because values can leak through shell history and release metadata. |
| External Secrets Operator | `externalSecrets.enabled=true` | Chart creates an ExternalSecret that writes `<release>-stampbot-external`. |

## EKS and IRSA

Use one of these service account paths.

For a pre-created service account from `eksctl`, disable chart service account creation:

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

For a Helm-created service account, provide the AWS role ARN:

```bash
helm upgrade --install stampbot charts/stampbot \
  --namespace stampbot \
  --set awsSecretsManager.enabled=true \
  --set awsSecretsManager.roleArn=arn:aws:iam::123456789012:role/stampbot-secrets \
  --set externalSecrets.enabled=true \
  --set externalSecrets.secretStore.name=aws-secrets-manager
```

When `awsSecretsManager.enabled=true`, `awsSecretsManager.roleArn` is required. The chart
fails rendering instead of emitting an empty `eks.amazonaws.com/role-arn` annotation.

## Validation

The chart includes `values.schema.json`; Helm uses it during `helm lint`, `helm install`,
and `helm upgrade`.

```bash
helm lint charts/stampbot
helm template stampbot charts/stampbot \
  --set github.existingSecret=stampbot-github
helm template stampbot charts/stampbot \
  --set awsSecretsManager.enabled=true \
  --set awsSecretsManager.roleArn=arn:aws:iam::123456789012:role/stampbot-secrets \
  --set externalSecrets.enabled=true
```

After installation:

```bash
kubectl get pods --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
kubectl logs deployment/stampbot --namespace stampbot
kubectl port-forward svc/stampbot 8000:80 --namespace stampbot
curl http://127.0.0.1:8000/health
```

### Verifying the deployment with `helm test`

The chart ships post-install test hooks. After installing, run:

```bash
helm test stampbot --namespace stampbot --logs
```

This launches two short-lived Pods that exercise the running release from
inside the cluster:

- **`test-connection`** — checks `/health`, `/ready`, `/metrics`, and `/` are
  reachable and healthy.
- **`test-webhook`** — verifies the `/webhook` signature path end to end: a
  correctly HMAC-signed `ping` is accepted (200) and a tampered signature is
  rejected (401). It reads the same webhook secret the deployment uses, and
  skips automatically if the release is not configured yet (setup mode).

For IRSA:

```bash
kubectl get serviceaccount stampbot --namespace stampbot -o yaml
kubectl get externalsecret --namespace stampbot
kubectl get secret stampbot-external --namespace stampbot
```

## Values

| Value | Type | Default | Description |
| --- | --- | --- | --- |
| `replicaCount` | integer | `2` | Deployment replicas when HPA is disabled. |
| `image.repository` | string | `ghcr.io/dannysauer/stampbot` | Container image repository. |
| `image.pullPolicy` | enum | `IfNotPresent` | Kubernetes image pull policy. |
| `image.tag` | string | chart `appVersion` | Container tag. |
| `image.digest` | string | `""` | Optional `sha256:...` digest. When set, the chart renders `repository@digest` and ignores `image.tag`. |
| `imagePullSecrets` | list | `[]` | Image pull Secrets. |
| `nameOverride` | string | `""` | Override chart name. |
| `fullnameOverride` | string | `""` | Override generated resource names. |
| `serviceAccount.create` | boolean | `true` | Create a ServiceAccount. |
| `serviceAccount.annotations` | map | `{}` | ServiceAccount annotations. |
| `serviceAccount.name` | string | `""` | Existing or custom ServiceAccount name. |
| `deploymentAnnotations` | map | `{}` | Deployment metadata annotations. |
| `podAnnotations` | map | Prometheus scrape annotations | Pod template annotations. |
| `podSecurityContext` | map | non-root defaults | Pod security context. |
| `securityContext` | map | restricted defaults | Container security context. |
| `service.type` | enum | `ClusterIP` | Kubernetes Service type. |
| `service.port` | integer | `80` | Service port. |
| `service.targetPort` | integer | `8000` | Application container port. |
| `service.annotations` | map | `{}` | Service annotations. |
| `metrics.enabled` | boolean | `true` | Expose `/metrics` on the service. |
| `metrics.serviceMonitor.enabled` | boolean | `false` | Create a ServiceMonitor. |
| `metrics.serviceMonitor.interval` | string | `30s` | Prometheus scrape interval. |
| `metrics.serviceMonitor.scrapeTimeout` | string | `10s` | Prometheus scrape timeout. |
| `ingress.enabled` | boolean | `false` | Create an Ingress. |
| `ingress.className` | string | `""` | Ingress class name. |
| `ingress.annotations` | map | `{}` | Ingress annotations. |
| `ingress.hosts` | list | `stampbot.local` | Host and path routing rules. |
| `ingress.tls` | list | `[]` | TLS entries. |
| `resources` | map | CPU and memory defaults | Container requests and limits. |
| `autoscaling.enabled` | boolean | `true` | Create an HPA. |
| `autoscaling.minReplicas` | integer | `2` | HPA minimum replicas. |
| `autoscaling.maxReplicas` | integer | `10` | HPA maximum replicas. |
| `autoscaling.targetCPUUtilizationPercentage` | integer | `80` | HPA CPU target. |
| `autoscaling.customMetrics.enabled` | boolean | `false` | Add custom HPA metrics. |
| `autoscaling.customMetrics.metrics` | list | request-count example | Raw HPA metric specs. |
| `vpa.enabled` | boolean | `false` | Create a VerticalPodAutoscaler. |
| `vpa.updateMode` | enum | `Auto` | VPA update mode: `Off`, `Initial`, `Recreate`, or `Auto`. |
| `vpa.minAllowed` | map | `50m`, `64Mi` | Minimum VPA recommendations. |
| `vpa.maxAllowed` | map | `1000m`, `1Gi` | Maximum VPA recommendations. |
| `nodeSelector` | map | `{}` | Node selector. |
| `tolerations` | list | `[]` | Pod tolerations. |
| `affinity` | map | soft anti-affinity | Pod affinity rules. |
| `config.logLevel` | string | `INFO` | Sets `STAMPBOT_LOG_LEVEL`. |
| `config.otelEnabled` | boolean | `false` | Sets `STAMPBOT_OTEL_ENABLED`. |
| `config.otelEndpoint` | string | `""` | Sets `STAMPBOT_OTEL_ENDPOINT` when OTel is enabled. |
| `config.otelServiceName` | string | `stampbot` | Sets `STAMPBOT_OTEL_SERVICE_NAME` when OTel is enabled. |
| `setup.enabled` | boolean | `false` | Enables `/setup`. Keep disabled after initial setup. |
| `setup.baseUrl` | string | `""` | Sets `STAMPBOT_BASE_URL` for generated setup URLs. |
| `github.appId` | string | `""` | Inline `STAMPBOT_APP_ID`. Prefer `github.existingSecret`. |
| `github.privateKey` | string | `""` | Inline `STAMPBOT_PRIVATE_KEY`. Prefer `github.existingSecret`. |
| `github.webhookSecret` | string | `""` | Inline `STAMPBOT_WEBHOOK_SECRET`. Prefer `github.existingSecret`. |
| `github.existingSecret` | string | `""` | Secret containing all required `STAMPBOT_*` keys. |
| `awsSecretsManager.enabled` | boolean | `false` | Add an IRSA role annotation to the ServiceAccount. |
| `awsSecretsManager.secretName` | string | `stampbot/github-credentials` | Example AWS Secrets Manager secret name. |
| `awsSecretsManager.region` | string | `us-east-1` | AWS region for examples and SecretStore references. |
| `awsSecretsManager.roleArn` | string | `""` | Required IRSA role ARN when AWS integration is enabled. |
| `externalSecrets.enabled` | boolean | `false` | Create an ExternalSecret. |
| `externalSecrets.secretStore.name` | string | `aws-secrets-manager` | SecretStore or ClusterSecretStore name. |
| `externalSecrets.secretStore.kind` | enum | `SecretStore` | Secret store kind. |
| `externalSecrets.refreshInterval` | string | `1h` | ExternalSecret refresh interval. |
| `externalSecrets.data` | list | three credential mappings | Secret key to remote reference mappings. |
| `opentelemetry.enabled` | boolean | `false` | Reserved OTel values block; use `config.*` for current env injection. |
| `opentelemetry.endpoint` | string | `http://otel-collector:4317` | Reserved endpoint value. |
| `opentelemetry.serviceName` | string | `stampbot` | Reserved service name value. |
| `podDisruptionBudget.enabled` | boolean | `true` | Create a PodDisruptionBudget. |
| `podDisruptionBudget.minAvailable` | integer/string | `1` | Minimum available pods. |
| `networkPolicy.enabled` | boolean | `false` | Create a NetworkPolicy. |
| `networkPolicy.policyTypes` | list | `Ingress`, `Egress` | NetworkPolicy policy types. |
| `networkPolicy.ingress` | list | namespace-selector example | Ingress policy rules. |
| `networkPolicy.egress` | list | namespace, pod, HTTPS, DNS rules | Egress policy rules. |
| `grafanaDashboard.enabled` | boolean | `false` | Create a Grafana dashboard ConfigMap. |
| `grafanaDashboard.labels` | map | `grafana_dashboard: "1"` | Dashboard ConfigMap labels. |
| `grafanaDashboard.annotations` | map | `{}` | Dashboard ConfigMap annotations. |

## Security Notes

- Prefer `github.existingSecret` or `externalSecrets.enabled` for credentials.
- Keep `setup.enabled=false` after the one-time GitHub App setup flow.
- Expose only the routes GitHub and operators need. If `/metrics` is publicly reachable,
  protect it at your ingress, load balancer, or service mesh.
- The default pod and container security contexts run as non-root, drop Linux capabilities,
  use the runtime default seccomp profile, and mount a writable `/tmp` only.
- Configure NetworkPolicy egress so Stampbot can reach GitHub API and DNS.
