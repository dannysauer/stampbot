# Verify a Stampbot release

Use this guide before you promote a Stampbot image or Helm chart. It verifies
the GitHub release, downloaded files, Sigstore identities, image attestations,
chart package, and Supply-chain Levels for Software Artifacts (SLSA)
provenance.

Run every command in Bash from a new, empty directory. Stop at the first
failure.

## Before you begin

Install these tools:

- [GitHub CLI](https://cli.github.com/) (`gh`) 2.93.0 or newer;
- [Cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3.0.6
  or newer;
- [SLSA verifier](https://github.com/slsa-framework/slsa-verifier#installation)
  2.7.1 or newer;
- [crane](https://github.com/google/go-containerregistry/tree/main/cmd/crane)
  0.21.7 or newer; and
- [Helm](https://helm.sh/docs/intro/install/) 3.12 or newer.

You also need outbound HTTPS access and an authenticated GitHub CLI session:

```bash
gh auth status
```

The commands below were run successfully on 2026-07-15 UTC with `gh` 2.93.0,
Cosign 3.0.6, SLSA verifier 2.7.1, crane 0.21.7, and Helm 3.19.0. Cosign 2.6.3
can verify the attestations when you specify their types, but its `cosign tree`
output doesn't discover these Open Container Initiative (OCI) referrers. Use
Cosign 3.0.6 or newer for the guide as written.

Set a known-complete app and chart pair:

```bash
set -euo pipefail

APP_VERSION=1.11.9
CHART_VERSION=0.13.12
RELEASE_REPOSITORY=dannysauer/stampbot
PACKAGE_OWNER=dannysauer
SIGSTORE_ISSUER=https://token.actions.githubusercontent.com

APP_IDENTITY="https://github.com/${RELEASE_REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
CHART_IDENTITY="https://github.com/${RELEASE_REPOSITORY}/.github/workflows/chart-release.yml@refs/heads/main"
SOURCE_URI="github.com/${RELEASE_REPOSITORY}"
IMAGE="ghcr.io/${RELEASE_REPOSITORY}"
DOCKERHUB_IMAGE=docker.io/stampbot/stampbot
CHART_OCI="oci://ghcr.io/${PACKAGE_OWNER}/charts/stampbot"
```

These coordinates are part of the signed identity, not cosmetic links. If the
repository or packages move, use the source, workflow identity, and registry
recorded by the release you are verifying. Don't replace a historical identity
only because GitHub redirects its old URL.

App `1.11.9` and chart `0.13.12` are the first automatic post-recovery pair
verified end to end. Both tags resolve to signed commit
`38e6dc705d3caedcf38a772192112c4888be9a3e`; the app release has its five
expected assets, and the chart release has its three expected assets.

This pair demonstrates a complete evidence path. It isn't a recommendation to
deploy those versions. Check current security findings, compatibility, and
release notes separately before promotion.

Do not use chart `0.13.10` as a known-complete example. Its GitHub release asset
and OCI package differ. [Issue #273](https://github.com/dannysauer/stampbot/issues/273)
tracks its cleanup, historical split releases, and the remaining provenance and
signature policy. Never substitute another tag until its actual asset list
passes the checks below.

## Inspect the releases

List the app assets:

```bash
gh release view "v${APP_VERSION}" \
  --repo "${RELEASE_REPOSITORY}" \
  --json tagName,publishedAt,isImmutable,assets \
  --jq '{
    tag: .tagName,
    published: .publishedAt,
    immutable: .isImmutable,
    assets: [.assets[].name]
  }'
```

List the chart assets:

```bash
gh release view "chart-v${CHART_VERSION}" \
  --repo "${RELEASE_REPOSITORY}" \
  --json tagName,publishedAt,isImmutable,assets \
  --jq '{
    tag: .tagName,
    published: .publishedAt,
    immutable: .isImmutable,
    assets: [.assets[].name]
  }'
```

The selected releases must be immutable and contain these files:

| Release | Required assets |
| --- | --- |
| App `v1.11.9` | `sbom.spdx.json`, `sbom.spdx.json.sigstore.json`, `stampbot-1.11.9.vex.json`, `stampbot-1.11.9.vex.json.sigstore.json`, and `stampbot-1.11.9.intoto.jsonl` |
| Chart `chart-v0.13.12` | `stampbot-0.13.12.tgz`, `stampbot-0.13.12.tgz.sigstore.json`, and `stampbot-chart-0.13.12.intoto.jsonl` |

Stop if either release is mutable or an asset required by your policy is
missing. A release title or version number isn't evidence that every publishing
job finished.

Verify GitHub's signed attestation for each immutable release:

```bash
gh release verify "v${APP_VERSION}" \
  --repo "${RELEASE_REPOSITORY}"

gh release verify "chart-v${CHART_VERSION}" \
  --repo "${RELEASE_REPOSITORY}"
```

Both commands should report that the release was verified and list every asset
with its digest. This check authenticates GitHub's claim about the immutable tag
and published asset set. It doesn't replace the signer and provenance checks
below.

## Download and authenticate the assets

Download the software bill of materials (SBOM), OpenVEX document, chart, and
their verification material:

```bash
mkdir app-release chart-release

gh release download "v${APP_VERSION}" \
  --repo "${RELEASE_REPOSITORY}" \
  --dir app-release \
  --pattern 'sbom.spdx.json*' \
  --pattern "stampbot-${APP_VERSION}.vex.json*" \
  --pattern "stampbot-${APP_VERSION}.intoto.jsonl"

gh release download "chart-v${CHART_VERSION}" \
  --repo "${RELEASE_REPOSITORY}" \
  --dir chart-release \
  --pattern "stampbot-${CHART_VERSION}.tgz*" \
  --pattern "stampbot-chart-${CHART_VERSION}.intoto.jsonl"
```

Confirm that each local file has the digest recorded in GitHub's release
attestation:

```bash
for ASSET in app-release/*; do
  gh release verify-asset "v${APP_VERSION}" "${ASSET}" \
    --repo "${RELEASE_REPOSITORY}"
done

for ASSET in chart-release/*; do
  gh release verify-asset "chart-v${CHART_VERSION}" "${ASSET}" \
    --repo "${RELEASE_REPOSITORY}"
done
```

Every iteration should report `Verification succeeded`.

Verify that Stampbot's app release workflow signed the SBOM and OpenVEX
document:

```bash
cosign verify-blob \
  --bundle app-release/sbom.spdx.json.sigstore.json \
  --certificate-identity "${APP_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  app-release/sbom.spdx.json

cosign verify-blob \
  --bundle "app-release/stampbot-${APP_VERSION}.vex.json.sigstore.json" \
  --certificate-identity "${APP_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  "app-release/stampbot-${APP_VERSION}.vex.json"
```

Verify that the chart release workflow signed the chart package:

```bash
cosign verify-blob \
  --bundle "chart-release/stampbot-${CHART_VERSION}.tgz.sigstore.json" \
  --certificate-identity "${CHART_IDENTITY}" \
  --certificate-oidc-issuer "${SIGSTORE_ISSUER}" \
  "chart-release/stampbot-${CHART_VERSION}.tgz"
```

Each command should end with `Verified OK`. The certificate identity pins the
artifact to one workflow file on the repository's `main` branch.

## Pin and verify the app image

Resolve the GitHub Container Registry (GHCR) tag to an immutable digest:

```bash
DIGEST="$(crane digest "${IMAGE}:${APP_VERSION}")"
printf 'Pinned image: %s@%s\n' "${IMAGE}" "${DIGEST}"
```

Discover the OCI referrers attached to that digest:

```bash
cosign tree "${IMAGE}@${DIGEST}"
```

The example digest has two attestation referrers. Authenticate their predicate
types and signer:

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

Cosign should report a verified statement for each type. These attestations
bind their predicates to the GHCR digest.

Confirm that the Docker Hub mirror resolves to the same multi-platform index:

```bash
DOCKERHUB_DIGEST="$(
  crane digest "${DOCKERHUB_IMAGE}:${APP_VERSION}"
)"
test "${DOCKERHUB_DIGEST}" = "${DIGEST}"
```

Stampbot publishes the OCI attestations to GHCR, not to its Docker Hub mirror.
Don't infer that another registry has them, even when its tag resolves to the
same digest.

The only image-level signatures published for this digest are the signed SBOM
and OpenVEX attestations. Stampbot doesn't publish a separate simple image
signature. Cosign 3 may let `cosign verify` succeed by verifying those
attestation signatures, so enforce the expected signature type explicitly. If
your policy requires a separate image signature, stop promotion or record an
approved exception.

## Inspect and compare the chart

Inspect the authenticated package before installation:

```bash
helm show chart "chart-release/stampbot-${CHART_VERSION}.tgz"
helm show values "chart-release/stampbot-${CHART_VERSION}.tgz"
helm template stampbot "chart-release/stampbot-${CHART_VERSION}.tgz" \
  --set github.existingSecret=stampbot-github
```

The chart metadata should report chart `0.13.12` and app `1.11.9` for this pair.
Rendering with an existing Secret name checks the chart without putting
credentials in the command or output.

Pull the same chart version from GHCR:

```bash
mkdir oci-chart
helm pull "${CHART_OCI}" \
  --version "${CHART_VERSION}" \
  --destination oci-chart
```

Compare the release asset and OCI package byte for byte:

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

Matching bytes extend the GitHub asset verification to the package pulled from
the OCI registry. A mismatch breaks that link.

Pin the verified app digest when you deploy the chart:

```yaml
image:
  repository: ghcr.io/dannysauer/stampbot
  digest: REPLACE_WITH_THE_RESOLVED_DIGEST
```

Replace `REPLACE_WITH_THE_RESOLVED_DIGEST` with only the value in `DIGEST`,
including its `sha256:` prefix. The chart gives `image.digest` precedence over
`image.tag` and its packaged `appVersion`.

Pinning prevents a later tag change from altering the deployed bytes. It
doesn't prove who built those bytes; the attestation checks provide that
evidence.

## Verify SLSA provenance

Verify that the app provenance covers both release documents:

```bash
slsa-verifier verify-artifact app-release/sbom.spdx.json \
  --provenance-path "app-release/stampbot-${APP_VERSION}.intoto.jsonl" \
  --source-uri "${SOURCE_URI}" \
  --source-branch main

slsa-verifier verify-artifact \
  "app-release/stampbot-${APP_VERSION}.vex.json" \
  --provenance-path "app-release/stampbot-${APP_VERSION}.intoto.jsonl" \
  --source-uri "${SOURCE_URI}" \
  --source-branch main
```

Verify the chart provenance:

```bash
slsa-verifier verify-artifact \
  "chart-release/stampbot-${CHART_VERSION}.tgz" \
  --provenance-path "chart-release/stampbot-chart-${CHART_VERSION}.intoto.jsonl" \
  --source-uri "${SOURCE_URI}" \
  --source-branch main
```

Each command should report `SLSA verification passed`. Stampbot's workflows run
from `main` and create release tags during the run, so the provenance records
the source branch rather than a tag ref.

## Record the promotion decision

Before you promote the release, record:

- the app and chart tags;
- the GitHub release URLs and publish times;
- the immutable-release and local-asset verification results;
- the certificate identities used for each blob;
- the GHCR image digest;
- the Docker Hub mirror comparison result;
- the verified OCI attestation types;
- the OCI chart comparison result;
- the SLSA source repository, branch, commit, and builder; and
- any approved exception for the missing separate image signature.

Record failures and unavailable evidence too. A useful decision says what you
couldn't verify.

## Troubleshoot verification

| Symptom | Action |
| --- | --- |
| `gh release verify` reports that no Sigstore verifier could be initialized | Confirm that your home directory and `~/.sigstore` are writable, then retry with outbound HTTPS access. |
| An expected asset is absent | Stop. Select a release with the required evidence or obtain an explicit policy exception. |
| Cosign reports a certificate identity mismatch after a repository move | Use the workflow identity embedded in that release's certificate. A redirect doesn't rewrite a historical signature. |
| `cosign tree` shows referrers but a typed verification fails | Treat the referrer as untrusted; discovery isn't authentication. |
| The OCI chart differs from the GitHub asset | Don't install the OCI package as the verified chart. |
| SLSA verification reports a source mismatch | Confirm the release's original repository and branch. Don't weaken the expected source to make verification pass. |

The release is ready for promotion only when every check required by your
policy passes and every exception has an owner and approval.
