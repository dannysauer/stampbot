# Verify a Stampbot release

Use this guide before promoting an app image or Helm chart. The commands use
Bash and should be run in a new, empty directory.

The examples use app `1.11.1` and chart `0.13.5` because every verification
path on this page was checked against those immutable releases on 2026-07-15
UTC.

## Install the tools

You need:

- [GitHub CLI](https://cli.github.com/) (`gh`) 2.93.0 or newer to inspect,
  download, and verify GitHub releases;
- [Cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3.0.6
  or newer to verify Sigstore bundles and OCI attestations;
- [SLSA verifier](https://github.com/slsa-framework/slsa-verifier#installation)
  to verify provenance;
- [crane](https://github.com/google/go-containerregistry/tree/main/cmd/crane)
  to resolve a container digest; and
- [Helm](https://helm.sh/docs/intro/install/) to pull and inspect the OCI chart.

The example commands were checked with `gh` 2.93.0, `cosign` 3.0.6,
`slsa-verifier` 2.7.1, `crane` 0.21.7, and Helm 4.2.3. Cosign 2.6.3 can
verify these attestations when given a type, but its `cosign tree` command
doesn't list their OCI referrers. Use Cosign 3.0.6 or newer to run this guide
as written.

You also need outbound HTTPS access and a GitHub-authenticated CLI session.
Run `gh auth status` to confirm access before continuing.

Set the versions:

```bash
set -euo pipefail

APP_VERSION=1.11.1
CHART_VERSION=0.13.5
REPOSITORY=dannysauer/stampbot
SIGSTORE_ISSUER=https://token.actions.githubusercontent.com
APP_IDENTITY=https://github.com/dannysauer/stampbot/.github/workflows/release.yml@refs/heads/main
CHART_IDENTITY=https://github.com/dannysauer/stampbot/.github/workflows/chart-release.yml@refs/heads/main
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
| App `v1.11.1` | `sbom.spdx.json`, `sbom.spdx.json.sigstore.json`, `stampbot-1.11.1.vex.json`, its `.sigstore.json` bundle, and `stampbot-1.11.1.intoto.jsonl` |
| Chart `chart-v0.13.5` | `stampbot-0.13.5.tgz`, its `.sigstore.json` bundle, and `stampbot-chart-0.13.5.intoto.jsonl` |

Stop if the asset list differs from the verification path you plan to use.

Verify GitHub's signed attestations for both immutable releases:

```bash
gh release verify "v${APP_VERSION}" --repo "${REPOSITORY}"
gh release verify "chart-v${CHART_VERSION}" --repo "${REPOSITORY}"
```

Both commands should report that the release was verified.

These commands verify GitHub's signed claim for each immutable release tag and
its published asset names and digests. The Sigstore and SLSA checks below
separately authenticate build identities and provenance.

## Verify the app release blobs

Download the software bill of materials (SBOM), OpenVEX document, and their
bundles:

```bash
mkdir app-release
gh release download "v${APP_VERSION}" \
  --repo "${REPOSITORY}" \
  --dir app-release \
  --pattern 'sbom.spdx.json*' \
  --pattern "stampbot-${APP_VERSION}.vex.json*" \
  --pattern "stampbot-${APP_VERSION}.intoto.jsonl"
```

Confirm that every downloaded file matches GitHub's immutable-release
attestation:

```bash
for asset in app-release/*; do
  gh release verify-asset "v${APP_VERSION}" "${asset}" \
    --repo "${REPOSITORY}" || exit 1
done
```

Each iteration should report `Verification succeeded`.

Verify the SBOM:

```bash
cosign verify-blob \
  --bundle app-release/sbom.spdx.json.sigstore.json \
  --certificate-identity "${APP_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  app-release/sbom.spdx.json
```

Verify the OpenVEX document:

```bash
cosign verify-blob \
  --bundle "app-release/stampbot-${APP_VERSION}.vex.json.sigstore.json" \
  --certificate-identity "${APP_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  "app-release/stampbot-${APP_VERSION}.vex.json"
```

Each command should end with `Verified OK`. The certificate identity is pinned
to Stampbot's release workflow on `main`.

## Pin the app image

Resolve the GHCR tag to an immutable digest:

```bash
IMAGE=ghcr.io/dannysauer/stampbot
DIGEST="$(crane digest "${IMAGE}:${APP_VERSION}")"
printf 'Pinned image: %s@%s\nHelm digest: %s\n' \
  "${IMAGE}" "${DIGEST}" "${DIGEST}"
```

Use that digest in Helm:

```yaml
image:
  repository: ghcr.io/dannysauer/stampbot
  digest: REPLACE_WITH_THE_RESOLVED_DIGEST
```

Replace `REPLACE_WITH_THE_RESOLVED_DIGEST` with only the `Helm digest` value
printed above, including its `sha256:` prefix.

Pinning prevents a tag change from altering the deployed bytes. It doesn't, by
itself, prove who built those bytes.

## Verify the GHCR image attestations

List the OCI referrers attached to the pinned digest:

```bash
cosign tree "${IMAGE}@${DIGEST}"
```

For app `1.11.1`, the tree contains SBOM and OpenVEX attestations. Verify each
attestation against Stampbot's release workflow:

```bash
cosign verify-attestation \
  --type spdxjson \
  --certificate-identity "${APP_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  "${IMAGE}@${DIGEST}"

cosign verify-attestation \
  --type openvex \
  --certificate-identity "${APP_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  "${IMAGE}@${DIGEST}"
```

These attestations bind their predicates to the GHCR digest. Stampbot publishes
them to GHCR, not to the Docker Hub mirror; don't infer that another registry
has the attestations even when its image tag resolves to the same digest.

The GHCR image doesn't include a standalone image signature. If your policy
requires one in addition to the verified attestations, record that requirement
as unmet.

## Verify the chart release blob

Download the chart and its bundle:

```bash
mkdir chart-release
gh release download "chart-v${CHART_VERSION}" \
  --repo "${REPOSITORY}" \
  --dir chart-release \
  --pattern "stampbot-${CHART_VERSION}.tgz*" \
  --pattern "stampbot-chart-${CHART_VERSION}.intoto.jsonl"
```

Confirm that all three files match the immutable-release attestation:

```bash
for asset in chart-release/*; do
  gh release verify-asset "chart-v${CHART_VERSION}" "${asset}" \
    --repo "${REPOSITORY}" || exit 1
done
```

Each iteration should report `Verification succeeded`.

Verify it:

```bash
cosign verify-blob \
  --bundle "chart-release/stampbot-${CHART_VERSION}.tgz.sigstore.json" \
  --certificate-identity "${CHART_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
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

Compare the packages byte for byte:

```bash
if cmp -s \
  "chart-release/stampbot-${CHART_VERSION}.tgz" \
  "oci-chart/stampbot-${CHART_VERSION}.tgz"; then
  printf 'Chart packages match.\n'
else
  printf 'Chart packages differ; do not install the OCI package.\n' >&2
  exit 1
fi
```

Matching bytes extend the verified GitHub blob to the package pulled from the OCI
registry. A mismatch means you must not install the OCI package as though it
were the verified release asset.

## Verify SLSA provenance

Verify that the app provenance covers both release documents:

```bash
slsa-verifier verify-artifact app-release/sbom.spdx.json \
  --provenance-path "app-release/stampbot-${APP_VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-branch main

slsa-verifier verify-artifact \
  "app-release/stampbot-${APP_VERSION}.vex.json" \
  --provenance-path "app-release/stampbot-${APP_VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-branch main
```

Verify the chart provenance:

```bash
slsa-verifier verify-artifact "chart-release/stampbot-${CHART_VERSION}.tgz" \
  --provenance-path "chart-release/stampbot-chart-${CHART_VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-branch main
```

Each command should report that SLSA provenance was verified. The workflows run
from `main` and create tags during the run, so the provenance records the source
branch rather than a tag ref.

`chart-v0.13.4` remains immutable. Both published assets pass GitHub
release-attestation verification, and the chart package passes Cosign
verification using its bundle. The workflow's SLSA upload was rejected because
it ran after publication. Use `chart-v0.13.5`; for newer releases, first confirm
and verify a matching `.intoto.jsonl` asset.

## Record the decision

Before promotion, record:

- app and chart tags;
- GitHub release URLs and publish times;
- immutable-release and downloaded-asset verification results;
- verified blob identities;
- GHCR image digest;
- verified OCI attestation types;
- OCI chart hash comparison;
- SLSA source repository, branch, commit, and builder; and
- any policy exception for the missing standalone image signature.

That record says what you verified. It should also say what the release didn't
make possible.
