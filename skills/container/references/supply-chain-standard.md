# Supply-Chain Standard

Use this reference for immutable identity, metadata, vulnerability assessment,
SBOM, provenance, signing, and verification.

## Release Identity

- Publish meaningful version tags and retain the resulting digest.
- Prefer production deployment by digest, or by tag plus verified digest
  resolution.
- Do not use `:latest` for production deployment.
- Include OCI source, revision, version, creation time, description, license,
  and vendor annotations when the values exist.

## Required Evidence

- Scan the final image and application dependencies with the
  organization-selected tools and policy.
- Generate an SBOM for release images.
- Generate build provenance for release images.
- Sign only when the organization or admission policy requires it and the
  corresponding verification path exists.
- Verify signatures, provenance, expected issuer, and identity before
  deployment where enforcement exists.
- Record accepted vulnerabilities with identifier, reason, compensating
  control, owner, and review date.

## Ownership And Safety

The container skill defines requirements, inspects local evidence, and may run
already-selected local tools when explicitly requested. `$github-workflows`
owns workflow YAML. `$publish-image` owns release tags, registry pushes,
signing actions, publication waits, and published digest evidence.

Without `--allow-network`, audit helpers may use only locally available
artifacts and offline tool modes. Never refresh vulnerability databases, pull
attestations, contact a registry, sign, or push implicitly.

Do not assume that generating a signature is useful without a trusted identity,
verification policy, and enforcement point.

## Official Sources

- [Docker build attestations](https://docs.docker.com/build/metadata/attestations/)
- [Docker SBOM attestations](https://docs.docker.com/build/metadata/attestations/sbom/)
- [Docker provenance attestations](https://docs.docker.com/build/metadata/attestations/slsa-provenance/)
- [Kubernetes image names and digests](https://kubernetes.io/docs/concepts/containers/images/)
- [Sigstore Cosign verification](https://docs.sigstore.dev/cosign/verifying/verify/)
