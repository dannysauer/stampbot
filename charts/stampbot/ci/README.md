# Helm Chart CI Test Cases

This directory contains values files for Helm chart integration tests. Each file defines a test case that is automatically discovered and run in CI.

## How It Works

1. CI discovers all `*-values.yaml` files in this directory
2. Each file becomes a test case (e.g., `default-values.yaml` → test case `default`)
3. Test cases run in parallel, each in its own kind cluster
4. The chart is installed with the values file, the pod is waited on for
   readiness, and the release is verified with `helm test` (the chart-shipped
   `test-connection` and `test-webhook` hooks)

## Adding a New Test Case

1. Create a new file: `<name>-values.yaml`
2. Add the values you want to test
3. (Optional) Add a `<name>-setup.sh` hook for prerequisites — see below
4. Commit and push - CI will automatically pick it up

## Setup hooks (prerequisites)

If a case needs something installed in the cluster before `helm install` (for
example a CRD that one of the chart's templates depends on), ship a
`<name>-setup.sh` alongside the values file. The install job auto-discovers it
and runs it after the kind cluster and test secret exist but before the chart
is installed. Cases without a hook are installed directly.

Example: `servicemonitor-setup.sh` installs the Prometheus Operator
`ServiceMonitor` CRD so the chart's ServiceMonitor can be applied. Only the CRD
is installed (not the operator) — the case validates that the resource renders
and applies cleanly, not that scraping works end to end.

## Requirements for Test Cases

All test cases must:
- Set `github.existingSecret: stampbot-github` (CI creates this secret)
- Use `replicaCount: 1` for faster tests
- Disable `podDisruptionBudget` (single replica doesn't need it)
- Disable `autoscaling` **unless autoscaling is the feature under test** — a
  case may override these defaults for the specific feature it exercises (the
  `autoscaling` case enables the HPA, for instance).

## Current Test Cases

| File | Description |
|------|-------------|
| `default-values.yaml` | Minimal configuration with defaults |
| `ingress-values.yaml` | Ingress object created (no controller asserted) |
| `autoscaling-values.yaml` | HorizontalPodAutoscaler enabled (object installs; no metrics-server) |
| `networkpolicy-values.yaml` | NetworkPolicy enabled (renders/installs; kindnet does not enforce) |
| `servicemonitor-values.yaml` | ServiceMonitor enabled (CRD installed via `servicemonitor-setup.sh`) |

### Not an install case: External Secrets

External Secrets (`externalSecrets.enabled`) is intentionally **not** an install
case. The deployment would mount a secret that only a running External Secrets
Operator (plus a real secret backend) can materialize, so the pod could never
become Ready in a self-contained kind run. The chart's ExternalSecret template
is instead validated by `helm-unittest` (`tests/externalsecret_test.yaml`).
