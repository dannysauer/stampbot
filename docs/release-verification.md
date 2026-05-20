# Release Verification

Stampbot releases publish release notes and machine-readable security artifacts on GitHub
releases. App releases use tags like `v1.2.3`; chart releases use tags like
`chart-v1.2.3`.

## App Release Assets

App releases include:

- `sbom.spdx.json`
- `sbom.spdx.json.sigstore.json`
- `stampbot-VERSION.vex.json`
- `stampbot-VERSION.vex.json.sigstore.json`
- `stampbot-VERSION.intoto.jsonl` for releases created after SLSA provenance support

## Chart Release Assets

Chart releases include:

- `stampbot-VERSION.tgz`
- `stampbot-VERSION.tgz.sigstore.json`
- `stampbot-chart-VERSION.intoto.jsonl` for releases created after SLSA provenance support

## Verify Sigstore Bundles

Install `cosign`, download the asset and its `.sigstore.json` bundle from the GitHub
release, and verify the bundle against GitHub Actions as the OIDC issuer.

```bash
VERSION=1.2.3
cosign verify-blob \
  --bundle "stampbot-${VERSION}.vex.json.sigstore.json" \
  --certificate-identity-regexp 'https://github.com/dannysauer/stampbot/.github/workflows/.*@refs/heads/main' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "stampbot-${VERSION}.vex.json"
```

Use the same pattern for `sbom.spdx.json` and chart package bundles.

## Verify SLSA Provenance

Install `slsa-verifier`, download the artifact and matching `.intoto.jsonl` provenance
file, and verify the source repository and release tag.

```bash
VERSION=1.2.3
slsa-verifier verify-artifact "stampbot-${VERSION}.vex.json" \
  --provenance-path "stampbot-${VERSION}.intoto.jsonl" \
  --source-uri github.com/dannysauer/stampbot \
  --source-tag "v${VERSION}"
```

If a provenance file covers multiple artifacts, run `slsa-verifier verify-artifact` once
for each artifact you plan to trust.

## Container Images and Charts

Container images are published to GitHub Container Registry. Helm charts are published as
OCI artifacts under `ghcr.io/dannysauer/charts/stampbot`. Prefer immutable digests in
production deployment manifests.
