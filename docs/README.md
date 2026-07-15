# Stampbot documentation

Start with what you need to do. The pages below keep setup instructions,
operational procedures, reference facts, and design discussion separate.

## Install and deploy

| You need to… | Read |
| --- | --- |
| Create the GitHub App or choose a runtime | [Install Stampbot](../INSTALLATION.md) |
| Install, upgrade, or roll back on Kubernetes | [Helm chart guide](../charts/stampbot/README.md) |
| Deploy through GitHub Actions to Google Cloud Run | [Deploy to Cloud Run](deploy-gcp-cloudrun.md) |
| Check signatures and provenance before promotion | [Verify a release](release-verification.md) |

## Configure and operate

| You need to… | Read |
| --- | --- |
| Set service options or repository approval policy | [Configuration reference](configuration.md) |
| Look up routes, events, commands, metrics, or artifacts | [Interface reference](reference.md) |
| Diagnose a webhook, review, credential, or deployment failure | [Operations runbook](operations.md) |

## Understand the design

| You want to know… | Read |
| --- | --- |
| How a signed webhook becomes a GitHub review | [Architecture](architecture.md) |
| Which trust and failure properties changes must preserve | [Security requirements](security-requirements.md) |

The repository also vendors the
[Google Python Style Guide](external/google-python-style-guide.md). Automation
keeps that external reference current; it isn't part of the Stampbot product
guide.

Contribution, governance, roadmap, and vulnerability-reporting policies remain
at the [repository root](../README.md#find-the-rest).
