# Verify a Stampbot release

Use this guide before promoting an app image or Helm chart. Run the commands in
a new, empty directory.

The examples use app `1.11.0` and chart `0.13.3` because those published assets
were checked while this page was rewritten on 2026-07-14.

## Install the tools

You need:

- `gh` to inspect and download GitHub releases;
- `cosign` to verify Sigstore bundles;
- `crane` to resolve a container digest; and
- `helm` to pull and inspect the OCI chart.

Install `slsa-verifier` only when the release actually contains an
`.intoto.jsonl` provenance file.

Set the versions:

```bash
APP_VERSION=1.11.0
CHART_VERSION=0.13.3
REPOSITORY=dannysauer/stampbot
```

## Inspect before downloading

List the app assets:

```bash
gh release view "v${APP_VERSION}" \
  --repo "${REPOSITORY}" \
  --json tagName,publishedAt,assets \
  --jq '{tag: .tagName, published: .publishedAt, assets: [.assets[].name]}'
```

List the chart assets:

```bash
gh release view "chart-v${CHART_VERSION}" \
  --repo "${REPOSITORY}" \
  --json tagName,publishedAt,assets \
  --jq '{tag: .tagName, published: .publishedAt, assets: [.assets[].name]}'
```

For the example releases, you should find:

| Release | Published assets |
| --- | --- |
| App `v1.11.0` | `sbom.spdx.json`, its `.sigstore.json` bundle, `stampbot-1.11.0.vex.json`, and its bundle |
| Chart `chart-v0.13.3` | `stampbot-0.13.3.tgz` and its `.sigstore.json` bundle |

Stop if the asset list differs from the verification path you plan to use.

## Verify the app release blobs

Download the software bill of materials (SBOM), OpenVEX document, and their
bundles:

```bash
mkdir app-release
gh release download "v${APP_VERSION}" \
  --repo "${REPOSITORY}" \
  --dir app-release \
  --pattern 'sbom.spdx.json*' \
  --pattern "stampbot-${APP_VERSION}.vex.json*"
```

Verify the SBOM:

```bash
cosign verify-blob \
  --bundle app-release/sbom.spdx.json.sigstore.json \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/release.yml@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  app-release/sbom.spdx.json
```

Verify the OpenVEX document:

```bash
cosign verify-blob \
  --bundle "app-release/stampbot-${APP_VERSION}.vex.json.sigstore.json" \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/release.yml@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "app-release/stampbot-${APP_VERSION}.vex.json"
```

Each command should end with `Verified OK`. The certificate identity is pinned
to Stampbot's release workflow on `main`.

## Pin the app image

Resolve the GHCR tag to an immutable digest:

```bash
IMAGE=ghcr.io/dannysauer/stampbot
DIGEST="$(crane digest "${IMAGE}:${APP_VERSION}")"
printf '%s\n' "${IMAGE}@${DIGEST}"
```

Use that digest in Helm:

```yaml
image:
  repository: ghcr.io/dannysauer/stampbot
  digest: REPLACE_WITH_THE_RESOLVED_DIGEST
```

Replace `REPLACE_WITH_THE_RESOLVED_DIGEST` with the value printed above,
including its `sha256:` prefix.

Pinning prevents a tag change from altering the deployed bytes. It doesn't, by
itself, prove who built those bytes.

## Understand the current image-verification gap

As of 2026-07-14, `cosign tree ghcr.io/dannysauer/stampbot:1.11.0` reports no
supply-chain security artifacts. The release also has no standalone image
signature.

The signed SBOM and OpenVEX blobs can be authenticated, but their blob
signatures don't bind the GHCR digest to the release workflow. If your policy
requires image identity or provenance, don't treat a digest pin as a substitute.
Build from the tagged source in a trusted builder, or wait for a release that
publishes a verifiable image signature or attestation.

Check a newer image rather than assuming the gap remains:

```bash
cosign tree "${IMAGE}:${APP_VERSION}"
```

## Verify the chart release blob

Download the chart and its bundle:

```bash
mkdir chart-release
gh release download "chart-v${CHART_VERSION}" \
  --repo "${REPOSITORY}" \
  --dir chart-release \
  --pattern "stampbot-${CHART_VERSION}.tgz*"
```

Verify it:

```bash
cosign verify-blob \
  --bundle "chart-release/stampbot-${CHART_VERSION}.tgz.sigstore.json" \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/chart-release.yml@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "chart-release/stampbot-${CHART_VERSION}.tgz"
```

Inspect the package before installation:

```bash
helm show chart "chart-release/stampbot-${CHART_VERSION}.tgz"
helm show values "chart-release/stampbot-${CHART_VERSION}.tgz"
helm template stampbot "chart-release/stampbot-${CHART_VERSION}.tgz" \
  --set github.existingSecret=stampbot-github
```

## Compare the OCI chart

Pull the same version from GHCR:

```bash
mkdir oci-chart
helm pull oci://ghcr.io/dannysauer/charts/stampbot \
  --version "${CHART_VERSION}" \
  --destination oci-chart
```

Compare the package hashes:

```bash
sha256sum \
  "chart-release/stampbot-${CHART_VERSION}.tgz" \
  "oci-chart/stampbot-${CHART_VERSION}.tgz"
```

Matching hashes extend the verified GitHub blob to the bytes pulled from the OCI
registry. A mismatch means you must not install the OCI package as though it
were the verified release asset.

## Verify provenance only when it exists

The example releases don't contain `stampbot-1.11.0.intoto.jsonl` or
`stampbot-chart-0.13.3.intoto.jsonl`. The workflow source defines provenance
jobs, but a workflow definition isn't an artifact.

If a future app release lists a matching provenance file, download it and run:

```bash
slsa-verifier verify-artifact app-release/sbom.spdx.json \
  --provenance-path "app-release/stampbot-${APP_VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-tag "v${APP_VERSION}"
```

For a future chart release:

```bash
slsa-verifier verify-artifact "chart-release/stampbot-${CHART_VERSION}.tgz" \
  --provenance-path "chart-release/stampbot-chart-${CHART_VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-tag "chart-v${CHART_VERSION}"
```

Don't run these commands against a guessed filename. Confirm the provenance
asset in the GitHub release first.

## Record the decision

Before promotion, record:

- app and chart tags;
- GitHub release URLs and publish times;
- verified blob identities;
- GHCR image digest;
- OCI chart hash comparison;
- image-signature or attestation result; and
- any policy exception for missing image identity or provenance.

That record says what you verified. It should also say what the release didn't
make possible.
