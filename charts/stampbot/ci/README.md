# Helm Chart CI Test Cases

This directory contains values files for Helm chart integration tests. Each file defines a test case that is automatically discovered and run in CI.

## How It Works

1. CI discovers all `*-values.yaml` files in this directory
2. Each file becomes a test case (e.g., `default-values.yaml` → test case `default`)
3. Test cases run in parallel, each in its own kind cluster
4. The chart is installed with the values file and verified for health

## Adding a New Test Case

1. Create a new file: `<name>-values.yaml`
2. Add the values you want to test
3. Commit and push - CI will automatically pick it up

### Example: Testing with Ingress

Create `ingress-values.yaml`:

```yaml
replicaCount: 1
autoscaling:
  enabled: false
podDisruptionBudget:
  enabled: false

github:
  existingSecret: stampbot-github

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: stampbot.local
      paths:
        - path: /
          pathType: Prefix
```

## Requirements for Test Cases

All test cases must:
- Set `github.existingSecret: stampbot-github` (CI creates this secret)
- Use `replicaCount: 1` and `autoscaling.enabled: false` for faster tests
- Disable `podDisruptionBudget` (single replica doesn't need it)

## Current Test Cases

| File | Description |
|------|-------------|
| `default-values.yaml` | Minimal configuration with defaults |
