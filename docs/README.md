# Stampbot Documentation

These docs are written for GitHub readers. Backstage and TechDocs are intentionally out
of scope for this repository.

| Page | Type | Use it when |
| --- | --- | --- |
| [Configuration Reference](configuration.md) | Reference | You need exact app settings, `stampbot.toml` keys, GitHub App permissions, defaults, and failure behavior. |
| [Operations and Troubleshooting](operations.md) | Runbook | A webhook, approval, ChatOps command, deployment, metric, or GitHub permission is failing. |
| [Interface Reference](reference.md) | Reference | You need HTTP routes, webhook inputs, outputs, metrics, and external interfaces. |
| [Cloud Run Deployment](deploy-gcp-cloudrun.md) | How-to | You deploy Stampbot to Google Cloud Run with GitHub Actions. |
| [Architecture](architecture.md) | Explanation | You need the request flow, component boundaries, trust boundaries, and outputs. |
| [Security Requirements](security-requirements.md) | Explanation | You need the security properties Stampbot is expected to preserve. |
| [Release Verification](release-verification.md) | How-to/reference | You verify release assets, SBOMs, VEX, SLSA provenance, chart packages, or container attestations. |
| [Google Python Style Guide](external/google-python-style-guide.md) | External reference | Vendored style-guide copy synchronized by workflow; not Stampbot product documentation. |

For installation paths, start with the root [README](../README.md) or
[INSTALLATION.md](../INSTALLATION.md). For the Helm chart as an installable package, see
[charts/stampbot/README.md](../charts/stampbot/README.md).
