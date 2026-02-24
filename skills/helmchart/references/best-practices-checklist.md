# Helm Chart Best Practices Checklist

Use this checklist while reviewing or generating charts.

## Metadata

- `Chart.yaml` includes:
  - `apiVersion: v2`
  - `type: application`
  - `version` and `appVersion`
  - `kubeVersion` when API compatibility matters
  - `maintainers`, `sources`, `home`, `keywords`

## Values and Interfaces

- `values.yaml` supports pinned image tags and optional digest pinning.
- `imagePullSecrets` and optional `global.imagePullSecrets` are supported.
- Secrets are externalized (`secretRef` or `existingSecret` patterns).
- Optional features are disabled by default.
- `values.schema.json` validates critical values and types.

## Template Quality

- `_helpers.tpl` centralizes:
  - naming
  - selectors
  - standard labels
  - image string helper
- Required values fail fast with clear error messages.
- `Deployment` includes:
  - stable selectors
  - security contexts
  - readiness/liveness split
  - graceful shutdown settings
  - resources
- Optional templates are gated by values and CRD availability:
  - ServiceMonitor
  - cert-manager resources
  - PDB
  - NetworkPolicy

## Security

- Pod and container security defaults are present unless incompatible.
- `automountServiceAccountToken` is disabled unless Kubernetes API is required.
- `readOnlyRootFilesystem` includes writable temp mount when app needs it.

## Reliability and HA

- Optional HPA with CPU and memory support.
- Optional PDB and topology spread/affinity controls.
- Strategy and revision history controls are explicit.

## Operability

- `templates/NOTES.txt` explains verify commands and dependencies.
- README includes install and production examples.
- README calls out optional dependencies (for example Prometheus Operator, cert-manager).

## CI and Testing

- `helm lint` enabled in CI.
- `helm template` smoke renders for default and key toggles.
- Optional `ct lint` for changed charts in PRs.
- Optional golden snapshot tests for deterministic rendered manifests.
- Prefix CI workflow job/check names with the project or chart name to keep
  status checks unambiguous in monorepos.
