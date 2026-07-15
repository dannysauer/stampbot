# Helm integration cases

This directory supplies values for clean-install and upgrade tests in
`.github/workflows/ci.yml`. Each install case gets its own kind cluster.

## How discovery works

CI finds every `*-values.yaml` file and removes the suffix to get the case name.
For example, `networkpolicy-values.yaml` becomes `networkpolicy`.

For each case, CI:

1. builds the working-tree Stampbot image;
2. creates a kind cluster and a namespace;
3. creates the fake `stampbot-github` test Secret;
4. runs an optional matching `NAME-setup.sh`;
5. installs the chart with `NAME-values.yaml`;
6. waits for the Pod and Deployment;
7. runs the chart's `helm test` hooks; and
8. prints workload and test-Pod logs on failure.

The cases run in parallel. One failure doesn't stop the others.

## Add a case

Create `NAME-values.yaml` with:

- `replicaCount: 1`;
- the local `stampbot:ci-test` image and `pullPolicy: Never`;
- `podDisruptionBudget.enabled: false`;
- `github.existingSecret: stampbot-github`; and
- `autoscaling.enabled: false` unless autoscaling is the feature under test.

Change only the values needed to exercise the new branch. A case should make
its boundary explicit: installing an object isn't the same as proving that its
controller performs useful work.

Run the chart checks before pushing:

```bash
make helm-test
```

CI discovers the new file without a workflow edit.

## Add a setup hook

When a feature needs a CRD or another prerequisite before Helm can install it,
add executable `NAME-setup.sh` beside the values file. CI runs the hook after
creating the cluster and test Secret.

Pin anything the hook downloads. Wait for a CRD to become Established before
installing a custom resource.

`servicemonitor-setup.sh` is the current example. It installs only the pinned
ServiceMonitor CRD, not the Prometheus Operator. The case proves that the
resource validates and applies; it doesn't prove that Prometheus scrapes it.

## Current coverage

| Case | What it proves | What it doesn't prove |
| --- | --- | --- |
| `default` | The minimal single-replica release installs and passes chart tests. | Production scaling or external routing. |
| `ingress` | The Ingress renders and applies. | An ingress controller, DNS, or TLS. |
| `autoscaling` | The HPA installs and points at the Deployment. | Scaling, because kind has no metrics server. |
| `networkpolicy` | The NetworkPolicy renders without blocking readiness in this cluster. | Enforcement, because kindnet doesn't enforce NetworkPolicy. |
| `servicemonitor` | The metrics listener and internal Service install. The ServiceMonitor validates against its real CRD. | End-to-end Prometheus discovery and scraping. |

External Secrets isn't a clean-install case. Without an operator and a real
backend, the generated Secret never appears and the Pod can't become ready.
`tests/externalsecret_test.yaml` covers that template instead.

## Upgrade coverage

The upgrade matrix reads `chart-vX.Y.Z` tags. It selects the latest patch from
the newest three major/minor lines.

For each selected version, CI installs the published chart and points it at the
locally built image. It then upgrades to the working-tree chart and reruns the
chart tests.

Keeping the image constant isolates chart compatibility. These cases catch
immutable field changes, renamed or removed values, selector drift, and broken
hook ordering that a clean install can miss.
