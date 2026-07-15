# Install Stampbot

A working installation has two parts: a Stampbot service and a GitHub App whose
webhook points at that service. Create the App once. Install it on each
repository Stampbot should serve.

Every production runtime needs a public HTTPS route to `POST /webhook`.

## Choose a runtime

| Runtime | Choose it when… | Go to |
| --- | --- | --- |
| Source checkout | You are developing Stampbot or testing setup. | [Run from source](#run-from-source) |
| Container | You already operate Docker or another OCI runtime. | [Run the container](#run-the-container) |
| Kubernetes | You want the supported chart, probes, scaling, and policy resources. | [Install with Helm](#install-with-helm) |
| Google Cloud Run | You want the repository's gated GitHub Actions deployment. | [Cloud Run guide](docs/deploy-gcp-cloudrun.md) |

## Create the GitHub App

Use the setup wizard when Stampbot already has a reachable URL. Create the App
manually when the App must exist first.

### Use the setup wizard

Setup is off by default. Start an unconfigured instance with these values:

```dotenv
STAMPBOT_SETUP_ENABLED=true
STAMPBOT_BASE_URL=https://stampbot.example.com
```

`STAMPBOT_BASE_URL` must be the public origin you control. Stampbot never uses
`Host` or `X-Forwarded-*` request headers to choose the manifest callback or
webhook destination. Plain HTTP is accepted only for localhost development.

> **Warning:** The callback displays the new private key and webhook secret.
> Restrict access to the setup route, save the values once, and disable setup
> before normal traffic reaches the service.

1. Open `https://stampbot.example.com/setup`.
2. Select **Create GitHub App**.
3. Choose the GitHub user or organization that will own the App.
4. Save the returned App ID, private key, and webhook secret in your secret
   store.
5. Configure those three values in the runtime.
6. Set `STAMPBOT_SETUP_ENABLED=false` and restart or roll out the service.
7. Install the App on its target repositories.

Setup closes as soon as all three credentials are present. Deliberate
reprovisioning also requires `STAMPBOT_SETUP_ALLOW_CONFIGURED=true`; leave that
break-glass setting off during normal operation.

If repositories inherit organization policy, install the App on the
organization's `.github` repository as well. Stampbot needs that installation
to read its `stampbot.toml`.

Check the configured service:

```bash
BASE_URL=https://stampbot.example.com
curl -fsS "${BASE_URL}/ready"
```

The response should report `configured: true` and `setup_enabled: false`.
Configured production instances return `403` from the setup routes.

### Create the App manually

Create a private GitHub App under the user or organization that will own it.
Set these fields:

| GitHub App field | Value |
| --- | --- |
| Homepage URL | The public Stampbot origin |
| Webhook URL | The public origin followed by `/webhook` |
| Webhook secret | A new random secret; store the same value as `STAMPBOT_WEBHOOK_SECRET` |
| Webhook active | Enabled |
| Public App | Disabled, unless you intend to serve installations outside the owner account |

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
`pull_request_review_comment`. Then record the App ID, generate a private key,
and install the App on its target repositories.

The [permission reference](docs/configuration.md#github-app-permissions)
explains each grant and its failure mode.

## Configure credentials

Every runtime needs these settings:

| Setting | Content |
| --- | --- |
| `STAMPBOT_APP_ID` | The numeric GitHub App ID |
| `STAMPBOT_PRIVATE_KEY` | A complete private-key PEM value or a path to a PEM file |
| `STAMPBOT_WEBHOOK_SECRET` | The secret configured on the GitHub App webhook |

Stampbot reads a private-key file only when it is a bounded regular file with a
complete PEM envelope. Kubernetes Secret symlink mounts are supported.

Keep credentials out of source control, command output, issue reports, and
container images. Production credentials belong in the runtime's secret store.

## Run from source

Install Git, Make, Poetry, and Python 3.11 or newer. Then clone and install the
development environment:

```bash
git clone https://github.com/dannysauer/stampbot.git
cd stampbot
make install-dev
```

Create an ignored `.env` file for a configured instance:

```dotenv
STAMPBOT_APP_ID=123456
STAMPBOT_PRIVATE_KEY=./private-key.pem
STAMPBOT_WEBHOOK_SECRET=replace-with-the-github-webhook-secret
STAMPBOT_SETUP_ENABLED=false
```

Store `private-key.pem` outside source control and restrict it to the process
owner. Start the development server:

```bash
make dev
```

Check liveness and readiness:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

`make dev` reloads changed code. For the container entrypoint without reload,
run `.venv/bin/python -m stampbot`.

GitHub can't reach `127.0.0.1`. Put port 8000 behind a public HTTPS tunnel for
real webhook tests. If you use the setup wizard, restart with that public origin
as `STAMPBOT_BASE_URL` before creating the App.

## Run the container

Choose and [verify](docs/release-verification.md) a release before promotion.
This verified example uses app release `1.11.9`:

```bash
APP_VERSION=1.11.9
```

Create a local environment file:

```dotenv
STAMPBOT_APP_ID=123456
STAMPBOT_PRIVATE_KEY=/run/secrets/stampbot-private-key.pem
STAMPBOT_WEBHOOK_SECRET=replace-with-the-github-webhook-secret
STAMPBOT_SETUP_ENABLED=false
STAMPBOT_LOG_FORMAT=json
```

Mount the key read-only and start the GHCR image:

```bash
docker run --rm \
  --name stampbot \
  --publish 8000:8000 \
  --env-file .env \
  --mount type=bind,src="${PWD}/private-key.pem",dst=/run/secrets/stampbot-private-key.pem,readonly \
  "ghcr.io/dannysauer/stampbot:${APP_VERSION}"
```

Check readiness from another terminal:

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

You need Kubernetes namespace access, Helm 3.12 or newer, and the three GitHub
App credentials. This verified example uses chart release `0.13.12`.

Read the webhook secret without putting it in shell history:

```bash
CHART_VERSION=0.13.12
kubectl create namespace stampbot

read -r -s -p "GitHub webhook secret: " STAMPBOT_WEBHOOK_SECRET
printf '\n'

kubectl create secret generic stampbot-github \
  --namespace stampbot \
  --from-literal=STAMPBOT_APP_ID=123456 \
  --from-file=STAMPBOT_PRIVATE_KEY=./private-key.pem \
  --from-literal=STAMPBOT_WEBHOOK_SECRET="${STAMPBOT_WEBHOOK_SECRET}"

unset STAMPBOT_WEBHOOK_SECRET
```

Create `values.yaml`:

```yaml
github:
  existingSecret: stampbot-github

setup:
  enabled: false
  allowConfigured: false

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

Install the OCI chart and wait for its Deployment:

```bash
helm install stampbot oci://ghcr.io/dannysauer/charts/stampbot \
  --version "${CHART_VERSION}" \
  --namespace stampbot \
  --values values.yaml \
  --wait \
  --timeout 5m
```

Run the chart tests:

```bash
kubectl rollout status deployment/stampbot --namespace stampbot
helm test stampbot --namespace stampbot --logs --timeout 2m
```

The tests call `/health`, `/ready`, and `/` from inside the cluster. When
`metrics.enabled=true`, they also check `/metrics` through its internal Service.
The webhook test sends one valid and one tampered signed `ping` payload. These
checks don't prove that public DNS, TLS, or the GitHub webhook reaches the
release.

Use the [chart guide](charts/stampbot/README.md) for upgrades, rollback,
External Secrets Operator, IRSA, autoscaling, monitoring, NetworkPolicy, and the
complete values reference.

If an upgrade fails, return to the last healthy Helm revision:

```bash
helm history stampbot --namespace stampbot
helm rollback stampbot PREVIOUS_REVISION --namespace stampbot --wait
kubectl rollout status deployment/stampbot --namespace stampbot
```

Replace `PREVIOUS_REVISION` with the revision from `helm history`. Helm can't
restore an external secret version, a removed CRD, or a GitHub App setting.

## Confirm GitHub delivery

Open the App's **Advanced** settings and inspect **Recent Deliveries**. Redeliver
a `ping` event after you set the webhook URL and secret.

A healthy response is:

```json
{"status":"ok","message":"pong"}
```

Create a pull request in a test repository and add one configured approval
label. The timeline should show an approval from your App.

If either check fails, continue in the [operations runbook](docs/operations.md).
