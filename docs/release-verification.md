# Release Verification

Stampbot publishes app releases from tags like `v1.2.3` and chart releases from tags
like `chart-v1.2.3`.

Use these commands from a clean directory. Install `cosign`, `slsa-verifier`, `gh`, and
optionally `crane` from `go-containerregistry`.

## Release Surfaces

| Surface | Location | Workflow |
| --- | --- | --- |
| App release assets | GitHub release `vVERSION` | `.github/workflows/release.yml` |
| GHCR image | `ghcr.io/dannysauer/stampbot:VERSION` | `.github/workflows/release.yml` |
| Docker Hub image | `docker.io/stampbot/stampbot:VERSION` | `.github/workflows/release.yml` |
| Helm chart package | GitHub release `chart-vVERSION` | `.github/workflows/chart-release.yml` |
| Helm chart OCI artifact | `oci://ghcr.io/dannysauer/charts/stampbot` | `.github/workflows/chart-release.yml` |

The release workflow attaches SBOM and VEX attestations to the GHCR image digest. Use the
GHCR image when verifying attestations. Docker Hub receives the same release tags from the
multi-registry image build, but the attestation examples below target GHCR.

## Download App Release Assets

```bash
VERSION=1.2.3
gh release download "v${VERSION}" \
  --repo dannysauer/stampbot \
  --pattern 'sbom.spdx.json*' \
  --pattern "stampbot-${VERSION}.vex.json*" \
  --pattern "stampbot-${VERSION}.intoto.jsonl"
```

App release assets:

| Asset | Purpose |
| --- | --- |
| `sbom.spdx.json` | SPDX JSON software bill of materials. |
| `sbom.spdx.json.sigstore.json` | Sigstore bundle for the SBOM blob. |
| `stampbot-VERSION.vex.json` | OpenVEX document. |
| `stampbot-VERSION.vex.json.sigstore.json` | Sigstore bundle for the VEX blob. |
| `stampbot-VERSION.intoto.jsonl` | SLSA provenance for release assets. |

## Verify Signed App Assets

```bash
VERSION=1.2.3

cosign verify-blob \
  --bundle sbom.spdx.json.sigstore.json \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/.*@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  sbom.spdx.json

cosign verify-blob \
  --bundle "stampbot-${VERSION}.vex.json.sigstore.json" \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/.*@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "stampbot-${VERSION}.vex.json"
```

## Verify App Release SLSA Provenance

```bash
VERSION=1.2.3

slsa-verifier verify-artifact sbom.spdx.json \
  --provenance-path "stampbot-${VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-tag "v${VERSION}"

slsa-verifier verify-artifact "stampbot-${VERSION}.vex.json" \
  --provenance-path "stampbot-${VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-tag "v${VERSION}"
```

## Verify Container Image Digest

Resolve the immutable GHCR image digest:

```bash
VERSION=1.2.3
IMAGE="ghcr.io/dannysauer/stampbot"
DIGEST="$(crane digest "${IMAGE}:${VERSION}")"
echo "${IMAGE}@${DIGEST}"
```

The release workflow does not publish a standalone image signature. Verify the SBOM and
VEX attestations attached to the image:

```bash
cosign verify-attestation "${IMAGE}@${DIGEST}" \
  --type spdxjson \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/release.yml@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify-attestation "${IMAGE}@${DIGEST}" \
  --type openvex \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/release.yml@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Pin the digest in Helm values:

```yaml
image:
  repository: ghcr.io/dannysauer/stampbot
  digest: sha256:REPLACE_WITH_VERIFIED_DIGEST
```

When `image.digest` is set, the chart renders `repository@digest` and ignores
`image.tag`.

## Download Chart Release Assets

```bash
VERSION=1.2.3
gh release download "chart-v${VERSION}" \
  --repo dannysauer/stampbot \
  --pattern "stampbot-${VERSION}.tgz*" \
  --pattern "stampbot-chart-${VERSION}.intoto.jsonl"
```

Chart release assets:

| Asset | Purpose |
| --- | --- |
| `stampbot-VERSION.tgz` | Packaged Helm chart. |
| `stampbot-VERSION.tgz.sigstore.json` | Sigstore bundle for the packaged chart. |
| `stampbot-chart-VERSION.intoto.jsonl` | SLSA provenance for the packaged chart. |

## Verify Chart Package

```bash
VERSION=1.2.3

cosign verify-blob \
  --bundle "stampbot-${VERSION}.tgz.sigstore.json" \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/.*@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "stampbot-${VERSION}.tgz"

slsa-verifier verify-artifact "stampbot-${VERSION}.tgz" \
  --provenance-path "stampbot-chart-${VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-tag "chart-v${VERSION}"
```

Inspect the chart before installation:

```bash
helm show chart "stampbot-${VERSION}.tgz"
helm show values "stampbot-${VERSION}.tgz"
helm template stampbot "stampbot-${VERSION}.tgz" \
  --set github.existingSecret=stampbot-github
```

## Verify OCI Chart Pull

```bash
VERSION=1.2.3
helm pull oci://ghcr.io/dannysauer/charts/stampbot \
  --version "${VERSION}"

sha256sum "stampbot-${VERSION}.tgz"
```

Compare the downloaded package hash to the hash covered by the chart SLSA provenance.

## Verification Checklist

- The GitHub release tag is the expected `vVERSION` or `chart-vVERSION`.
- `cosign verify-blob` succeeds for downloaded release assets.
- `slsa-verifier verify-artifact` succeeds against the matching source tag.
- The GHCR image digest is recorded and used for deployment.
- `cosign verify-attestation` succeeds for both `spdxjson` and `openvex`.
- `helm template` renders with your production values before upgrade.
