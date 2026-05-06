# Helm Chart Best Practices Checklist

Use this checklist while reviewing or generating charts.

## Metadata

- `Chart.yaml` includes:
  - `apiVersion: v2`
  - `type: application`
  - SemVer 2 `version` for the chart/package version
  - quoted informational `appVersion`
  - `kubeVersion` when API compatibility matters
  - `maintainers`, `sources`, `home`, `keywords`
- `appVersion` stays aligned with the default app/image version when known and stable.
- Non-SemVer app versions such as Git SHAs, build IDs, dates, and vendor release strings are not rewritten only to satisfy SemVer.

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
- Existing repository validators such as `kubeconform`, `kubeval`, `datree`, `ct`, `helm-unittest`, snapshot tests, and policy-as-code checks are reused before introducing new validators.

## Security

- Pod and container security defaults are present unless incompatible.
- `automountServiceAccountToken` is disabled unless Kubernetes API is required.
- `readOnlyRootFilesystem` includes writable temp mount when app needs it.
- Existing manifests, values, Dockerfile references, init containers, volume mounts, and documented runtime requirements are inspected before changing `securityContext`.
- Configurable secure defaults are preferred over hard-coded security settings that may break workloads.
- Writable paths such as `/tmp`, cache directories, and log directories are backed by `emptyDir` or documented external volumes when `readOnlyRootFilesystem` is enabled.

## RBAC

- `rbac` and `serviceAccount` values are separate.
- Defaults prefer:
  - `rbac.create: true`
  - `serviceAccount.create: true`
  - `serviceAccount.name: ""`
  - `serviceAccount.annotations: {}`
  - `serviceAccount.automountServiceAccountToken: false`
- Roles and RoleBindings are least-privilege and exist only when the workload needs Kubernetes API access.

## Reliability and HA

- Optional HPA with CPU and memory support.
- Optional PDB and topology spread/affinity controls.
- Strategy and revision history controls are explicit.

## Operability

- `templates/NOTES.txt` explains verify commands and dependencies.
- README includes install and production examples.
- README calls out optional dependencies (for example Prometheus Operator, cert-manager).

## CI and Testing

- Dependencies are resolved before lint/render:
  - `helm dependency build` when `Chart.lock` exists.
  - `helm dependency update` when `Chart.yaml` declares dependencies and no lock file exists.
  - Generated `Chart.lock` and `charts/` changes are inspected before they are kept.
- `helm lint --strict` enabled when warnings should block the change.
- `helm lint --with-subcharts` used when subcharts are in scope.
- `helm template smoke --debug` renders for default and key toggles.
- `helm template smoke --kube-version <supported-version>` runs when supported Kubernetes versions are known.
- Optional `ct lint` for changed charts in PRs.
- Optional golden snapshot tests for deterministic rendered manifests.
- Prefix CI workflow job/check names with the project or chart name to keep
  status checks unambiguous in monorepos.
