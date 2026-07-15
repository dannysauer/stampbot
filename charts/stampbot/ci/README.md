# Helm integration test cases

This directory defines the clean-install cases used by the
[Helm jobs in `ci.yml`](../../../.github/workflows/ci.yml). It is for chart
maintainers adding a Kubernetes feature or changing an install boundary.

The cases test the working-tree chart in separate kind clusters. They
complement the template assertions under `charts/stampbot/tests`; they do not
replace controller-level or production-cluster tests.

## How case discovery works

CI finds every `*-values.yaml` file in this directory. It removes
`-values.yaml` to get the case name. For example,
`networkpolicy-values.yaml` becomes `networkpolicy`.

An optional `NAME-setup.sh` file prepares cluster prerequisites for the
matching case. CI invokes it with Bash after creating the namespace and test
Secret but before installing the chart.

Each discovered case runs this sequence:

1. Check out the repository and install the pinned tools.
2. Create an isolated kind cluster named for the case.
3. Build `stampbot:ci-test` with the current commit version and load it into
   kind.
4. Create the `stampbot-test` namespace.
5. Create a fake `stampbot-github` Secret with the three required keys.
6. Run the matching setup script when one exists.
7. Install the chart with the case values and wait up to 120 seconds.
8. Wait for the Deployment and Pod to become ready.
9. Run both chart-shipped `helm test` hooks.
10. Print Pod descriptions and logs on failure, then remove the release and
    namespace.

Cases run in parallel with fail-fast disabled. One failure does not cancel the
others.

## Add an install case

If a template-only assertion can prove the behavior, add a Helm unit test
instead. Add an integration case when Kubernetes must accept the rendered
objects or when the running Pod and chart hooks matter.

Create `NAME-values.yaml` with this baseline:

```yaml
replicaCount: 1

image:
  repository: stampbot
  tag: ci-test
  pullPolicy: Never

autoscaling:
  enabled: false

podDisruptionBudget:
  enabled: false

github:
  existingSecret: stampbot-github
```

Keep `autoscaling.enabled=true` only when autoscaling is the feature under
test. Keep the HPA minimum at one in that case so the job does not wait for
extra Pods.

Add only the values needed for the branch you are testing. Do not copy
credentials, kubeconfigs, account identifiers, or production hostnames into a
case. The workflow supplies a clearly fake Secret.

Name the file with lowercase letters, digits, and hyphens. The discovery step
passes the basename into a kind cluster name and a file path.

CI discovers the new case without a workflow edit.

## Add a setup hook

If the chart renders a custom resource, install its CRD before `helm install`.
Create `NAME-setup.sh` beside the values file.

Use this shape:

```bash
#!/usr/bin/env bash
set -euo pipefail

CONTROLLER_VERSION="v1.2.3"
CRD_URL="https://raw.githubusercontent.com/example/controller/${CONTROLLER_VERSION}/config/crd.yaml"

kubectl apply --server-side --filename "${CRD_URL}"
kubectl wait \
  --for=condition=established \
  --timeout=60s \
  crd/widgets.example.com
```

The names and URL in that block are reserved examples. Replace them with the
real upstream project. Use an immutable commit or verify a release asset
checksum. Do not download from a moving branch.

Keep setup hooks idempotent. Install the smallest prerequisite that proves the
case, and wait for it before Helm references the custom resource. CI calls the
file through Bash, though the repository keeps executable scripts executable
for local use.

`servicemonitor-setup.sh` is the current example. It installs the
ServiceMonitor CRD from Prometheus Operator v0.86.0 and waits for the CRD to
become Established. It does not install the operator.

## Run local checks

From the repository root, install the tool versions in `.tool-versions`. You
also need Docker and kubeconform.

Run the chart lint, schema, manifest, and unit-test suite:

```bash
make helm-test
```

Render every integration values file through the schema and kubeconform:

```bash
for values_file in charts/stampbot/ci/*-values.yaml
do
  helm lint charts/stampbot --values "${values_file}"
  helm template stampbot charts/stampbot \
    --namespace stampbot-test \
    --values "${values_file}" \
    | kubeconform -strict -ignore-missing-schemas -summary
done
```

The kubeconform command ignores missing CRD schemas. The kind jobs remain the
source of truth for whether Kubernetes accepts those resources with the setup
hook applied.

## Current install coverage

The matrix currently discovers these cases:

| Case | What it proves | What it does not prove |
| --- | --- | --- |
| `default` | A single-replica release installs, becomes ready, and passes the chart hooks. | Production capacity, scaling, or public routing |
| `ingress` | Kubernetes accepts the rendered Ingress and the workload stays ready. | Ingress-controller reconciliation, DNS, or TLS |
| `autoscaling` | Kubernetes accepts the HPA and it targets the Stampbot Deployment. | Scaling decisions; kind has no Metrics Server |
| `networkpolicy` | Kubernetes accepts the policy, including its named metrics port, and the workload stays ready in kind. | Enforcement; kindnet does not enforce NetworkPolicy |
| `servicemonitor` | The metrics listener, metrics Service, and ServiceMonitor install against the real CRD. | Prometheus discovery or scraping; the operator is absent |

External Secrets Operator has no clean-install case. Without an operator and
backend, the target Secret never appears and the Pod cannot become ready.
`tests/externalsecret_test.yaml` covers the rendered object.

The VPA and Grafana dashboard are also template-tested rather than reconciled
by their controllers in kind.

## How upgrade coverage works

The upgrade matrix reads tags shaped like `chart-vX.Y.Z`. It keeps the latest
patch from each major.minor line, then selects the three newest lines.

For each selected version, CI:

1. Pulls the published chart from
   `oci://ghcr.io/dannysauer/charts/stampbot`.
2. Installs that chart with the locally built `stampbot:ci-test` image.
3. Upgrades the release to the working-tree chart with
   `default-values.yaml`.
4. Waits for the rollout and reruns the chart hooks.

Keeping the image constant isolates chart compatibility. The matrix catches
immutable-field changes, selector drift, hook ordering failures, and default
upgrade failures across recent chart lines.

It does not exercise every optional feature during upgrade. Add a focused
install case and template assertions for feature-specific migrations. If a
change needs controller reconciliation or stored external state, test it in an
environment that owns those dependencies.

## Diagnose a failed case

The job prints `kubectl describe` output and logs for Pods labeled
`app.kubernetes.io/name=stampbot`. Start with the first failed step, then use
this table to narrow the cause:

| Symptom | Likely boundary |
| --- | --- |
| `no matches for kind` | The case needs a CRD setup hook, or the hook did not wait for Established. |
| `ImagePullBackOff` | The case changed the local image name, tag, pull policy, or kind cluster name. |
| Pod reports `CreateContainerConfigError` | The selected Secret is absent or missing a required key. |
| `/ready` returns 503 | The credential values are empty and setup is disabled. |
| HPA reports unknown CPU | Expected in the autoscaling case; the job proves object installation, not scaling. |
| NetworkPolicy case passes but traffic fails elsewhere | kindnet did not enforce the policy; compare production namespace labels, Pod labels, ports, and CNI behavior. |
| ServiceMonitor exists but no target appears | The case installs only the CRD; inspect the real Prometheus Operator and its selectors. |
| Signed webhook hook fails | Compare the Deployment and hook Secret references, then inspect the application log for signature errors. |

Do not weaken readiness, signature, or schema checks to make a case pass.
Change the fixture when the fixture no longer models a valid installation.

The [chart operator guide](../README.md) describes the resources, values, and
security boundaries that these cases protect.
