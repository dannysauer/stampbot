# Install Stampbot

You need two things: a running Stampbot service and a GitHub App that points
webhooks at it. Create the App once, then install it on each repository that
should use Stampbot.

## Choose a runtime

| Runtime | Use it when | Instructions |
| --- | --- | --- |
| Source checkout | You are developing Stampbot or testing the setup flow. | [Run from source](#run-from-source) |
| Container | You already operate a container runtime. | [Run the container](#run-the-container) |
| Kubernetes | You want the supported chart, probes, scaling, and optional controllers. | [Install with Helm](#install-with-helm) |
| Google Cloud Run | You want this repository's GitHub Actions deployment. | [Cloud Run guide](docs/deploy-gcp-cloudrun.md) |

Every runtime needs a public HTTPS route to `POST /webhook` before GitHub can
deliver real events.

## Create the GitHub App

The setup wizard is the shortest path. Manual setup is available when you need
to create the App before Stampbot has a reachable URL.

### Use the setup wizard

Run Stampbot without App credentials and explicitly enable setup with a trusted
public URL:

```dotenv
STAMPBOT_SETUP_ENABLED=true
STAMPBOT_BASE_URL=https://stampbot.example.com
```

`STAMPBOT_BASE_URL` is required. Stampbot does not derive callback or webhook
destinations from `Host` or `X-Forwarded-*` request headers. HTTP is accepted
only for `localhost` development URLs.

Existing configured services continue serving webhooks without either setup
setting. An unconfigured service upgrading from an earlier release must add the
two values above before the wizard will open.

> **Protect the setup flow.** The callback shows the new private key and webhook
> secret in the browser. Use it once, save the credentials in your secret
> store, and disable setup before normal operation. Setup closes automatically
> after credentials are present unless the separate configured-instance
> override is enabled.

1. Open `BASE_URL/setup`.
2. Select **Create GitHub App**.
3. Choose the GitHub user or organization that will own the App.
4. Return to Stampbot and save the App ID, private key, and webhook secret.
5. Add those values to the runtime.
6. Set `STAMPBOT_SETUP_ENABLED=false` and restart or roll out the service.
7. Install the App on the repositories Stampbot should serve.

If you use organization-wide policy, install the App on the organization's
`.github` repository too. Stampbot needs access to read its `stampbot.toml`.

Check the finished service:

```bash
BASE_URL=https://stampbot.example.com
curl -fsS "${BASE_URL}/ready"
```

The readiness response should report `configured: true` and
`setup_enabled: false`. A normal configured instance returns `403` for every
`/setup` route.

### Create the App manually

Create a private GitHub App under the user or organization that will own it.
Use these settings:

| GitHub App setting | Value |
| --- | --- |
| Homepage URL | The public Stampbot origin. |
| Webhook URL | The public origin followed by `/webhook`. |
| Webhook secret | A new random secret. Store the same value as `STAMPBOT_WEBHOOK_SECRET`. |
| Webhook active | Enabled. |
| Public App | Disabled unless you intend to serve installations outside the owner account. |

Grant these repository permissions:

| Permission | Level |
| --- | --- |
| Pull requests | Read and write |
| Contents | Read-only |
| Metadata | Read-only |
| Issues | Read-only |
| Members | Read-only |
| Administration | Read-only |

Subscribe to `pull_request`, `issue_comment`, and
`pull_request_review_comment` events.

Record the App ID and generate a private key. Configure those values with the
webhook secret, then install the App on its target repositories.

The [configuration reference](docs/configuration.md#github-app-permissions)
explains why each permission exists and how its failure appears.

## Run from source

You need Git, Make, Poetry, and Python 3.11 or newer. Run these commands from a
shell on the machine that will host Stampbot:

```bash
git clone https://github.com/dannysauer/stampbot.git
cd stampbot
make install-dev
```

For a configured instance, create an ignored `.env` file:

```dotenv
STAMPBOT_APP_ID=123456
STAMPBOT_PRIVATE_KEY=./private-key.pem
STAMPBOT_WEBHOOK_SECRET=replace-with-the-github-webhook-secret
STAMPBOT_SETUP_ENABLED=false
```

Keep `private-key.pem` outside source control and readable only by the process
owner.

Start the service:

```bash
make dev
```

Then check both probes:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

`make dev` reloads code and is meant for development. Run
`.venv/bin/python -m stampbot` without reload when you need the same entrypoint
as the container.

GitHub can't reach `127.0.0.1`. Use a public HTTPS tunnel for webhook testing and
set `STAMPBOT_BASE_URL` before creating the App manifest.

## Run the container

Choose an app release that you have verified. This example uses `1.11.0`:

```bash
APP_VERSION=1.11.0
```

Create a local environment file:

```dotenv
STAMPBOT_APP_ID=123456
STAMPBOT_PRIVATE_KEY=/run/secrets/stampbot-private-key.pem
STAMPBOT_WEBHOOK_SECRET=replace-with-the-github-webhook-secret
STAMPBOT_SETUP_ENABLED=false
STAMPBOT_LOG_FORMAT=json
```

Run the GHCR image and mount the private key read-only:

```bash
docker run --rm \
  --name stampbot \
  --publish 8000:8000 \
  --env-file .env \
  --mount type=bind,src="${PWD}/private-key.pem",dst=/run/secrets/stampbot-private-key.pem,readonly \
  "ghcr.io/dannysauer/stampbot:${APP_VERSION}"
```

Verify the process from another terminal:

```bash
curl -fsS http://127.0.0.1:8000/ready
```

### Expose container metrics to the local host

The container leaves metrics disabled. If a Prometheus process on the Docker
host needs them, stop the container and add these values to `.env`:

```dotenv
STAMPBOT_METRICS_ENABLED=true
STAMPBOT_METRICS_HOST=0.0.0.0
STAMPBOT_METRICS_PORT=9090
```

The listener must use `0.0.0.0` inside the container for Docker port forwarding
to reach it. Keep the host-side publish address on loopback because the metrics
listener doesn't authenticate clients.

Add `--publish 127.0.0.1:9090:9090` to the earlier `docker run` command,
before `--env-file`.

Verify the dedicated listener from the Docker host:

```bash
curl -fsS http://127.0.0.1:9090/metrics
```

Remove the three variables and the `9090` publish when you no longer scrape
metrics.

To roll back, stop this container and run a previously verified version with
the same environment and secret mount. Changing the image doesn't restore a
rotated key or webhook secret.

## Install with Helm

You need a Kubernetes cluster, Helm 3.12 or newer, a `kubectl` context with
namespace access, and the three GitHub App credentials.

This example uses chart release `0.13.3`:

```bash
CHART_VERSION=0.13.3
kubectl create namespace stampbot
kubectl create secret generic stampbot-github \
  --namespace stampbot \
  --from-literal=STAMPBOT_APP_ID=123456 \
  --from-file=STAMPBOT_PRIVATE_KEY=./private-key.pem \
  --from-literal=STAMPBOT_WEBHOOK_SECRET=replace-with-the-github-webhook-secret
```

Store production settings in a values file:

```yaml
github:
  existingSecret: stampbot-github

setup:
  enabled: false
  allowConfigured: false
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

Install the OCI chart:

```bash
helm install stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --version "${CHART_VERSION}" \
  --namespace stampbot \
  --values values.yaml
```

Wait for the deployment and run the chart's tests:

```bash
kubectl rollout status deployment/stampbot --namespace stampbot
helm test stampbot --namespace stampbot --logs
```

The tests call `/health`, `/ready`, and `/` from inside the cluster. When
`metrics.enabled=true`, they also check `/metrics` through its internal Service.
The webhook test sends one valid and one tampered signed `ping` payload.

For External Secrets Operator, Amazon EKS IAM Roles for Service Accounts,
autoscaling, monitoring, NetworkPolicy, and every value, use the
[chart documentation](charts/stampbot/README.md).

If an upgrade fails:

```bash
helm history stampbot --namespace stampbot
helm rollback stampbot PREVIOUS_REVISION --namespace stampbot
kubectl rollout status deployment/stampbot --namespace stampbot
```

Replace `PREVIOUS_REVISION` with the last healthy revision shown by
`helm history`. A Helm rollback doesn't restore an external secret version or
GitHub App setting.

## Confirm GitHub delivery

Open the App's **Advanced** settings and inspect **Recent Deliveries**. Redeliver
a `ping` event after the webhook URL and secret are set.

A healthy response is:

```json
{"status":"ok","message":"pong"}
```

Then create a pull request in a test repository and add one configured approval
label. Confirm that the pull request timeline shows an approval from your App.

If either check fails, move to the [operations runbook](docs/operations.md).
