#!/usr/bin/env bash
# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0
#
# Per-test-case setup hook for the `servicemonitor` CI install case.
#
# The chart's ServiceMonitor is a monitoring.coreos.com/v1 custom resource, so
# `helm install` fails with "no matches for kind ServiceMonitor" unless that CRD
# exists in the cluster. We install only the ServiceMonitor CRD (not the full
# Prometheus operator): the stampbot pod does not depend on the operator to
# become Ready, and this case validates that the chart's ServiceMonitor applies
# cleanly against the real CRD schema — not that scraping works end to end.
set -euo pipefail

# Pinned Prometheus Operator release providing the CRD bundle.
PROMETHEUS_OPERATOR_VERSION="v0.86.0"
CRD_URL="https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/${PROMETHEUS_OPERATOR_VERSION}/example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml"

echo "Installing ServiceMonitor CRD from prometheus-operator ${PROMETHEUS_OPERATOR_VERSION}"
kubectl apply --server-side -f "${CRD_URL}"

# Wait for the CRD to be established before helm install references it.
kubectl wait --for=condition=established --timeout=60s \
  crd/servicemonitors.monitoring.coreos.com
