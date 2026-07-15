# Stampbot documentation

Start with the task in front of you. These pages are written for GitHub and don't
depend on a separate documentation site.

## Set up and deploy

| Task | Page | Type |
| --- | --- | --- |
| Create the GitHub App and choose a runtime | [Install Stampbot](../INSTALLATION.md) | How-to |
| Install or upgrade on Kubernetes | [Stampbot Helm chart](../charts/stampbot/README.md) | How-to and reference |
| Deploy with GitHub Actions to Google Cloud Run | [Deploy to Cloud Run](deploy-gcp-cloudrun.md) | How-to |
| Verify release assets and images | [Verify a release](release-verification.md) | How-to |

## Configure and operate

| Task | Page | Type |
| --- | --- | --- |
| Set app options or repository approval policy | [Configuration reference](configuration.md) | Reference |
| Look up routes, webhook behavior, commands, or metrics | [Interface reference](reference.md) | Reference |
| Diagnose a failed webhook, approval, or deployment | [Operations runbook](operations.md) | How-to |

## Understand the system

| Question | Page | Type |
| --- | --- | --- |
| How does a webhook become an approval? | [Architecture](architecture.md) | Explanation |
| Which security properties must changes preserve? | [Security requirements](security-requirements.md) | Normative explanation |

The [Google Python Style Guide](external/google-python-style-guide.md) is a
vendored external reference. An automated workflow keeps it synchronized; it
isn't Stampbot product documentation.

For contribution, governance, roadmap, and vulnerability-reporting policies,
return to the [repository README](../README.md#find-the-right-document).
