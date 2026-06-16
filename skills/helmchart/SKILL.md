---
name: helmchart
description: "Use for Helm chart work: create, review, harden, refactor, lint, template, or standardize Chart.yaml, values.yaml, values.schema.json, templates/, NOTES.txt, README, and chart CI. Do not use for OCI chart publication flow generation; use publish-helm. Do not use for plain Kubernetes YAML, Kustomize, Dockerfiles, or Terraform unless integrating with Helm."
---

# Helm Chart

Harden Helm charts for production and verify they render safely and consistently.

## Workflow

1. Identify chart scope
   - Locate target chart directories (`Chart.yaml`, `values.yaml`, `templates/`).
   - Confirm whether scope is one chart or a chart set.

2. Enforce chart structure baseline
   - Require:
     - `Chart.yaml`
     - `values.yaml`
     - `templates/_helpers.tpl`
   - Add when appropriate:
     - `values.schema.json`
     - `templates/NOTES.txt`
     - `README.md`
   - Keep selectors stable to avoid upgrade breakage.

3. Apply best practices to metadata and values
   - In `Chart.yaml`, prefer:
     - `apiVersion: v2`
     - `type: application`
     - SemVer 2 `version` for the chart/package version
     - Quoted `appVersion` for informational app metadata
     - Keep `version` independent from `appVersion`
     - Keep `appVersion` aligned with the default app/image version when that version is known and stable
     - Do not rewrite non-SemVer `appVersion` values such as Git SHAs, build IDs, dates, or vendor release strings only to satisfy SemVer
     - `kubeVersion` when API assumptions exist
     - `maintainers`, `sources`, `home`, `keywords`
   - In values:
     - Prefer immutable images (`image.tag` pinned; support `image.digest`)
     - Prefer digest-pinned deploys in production (`repo@sha256:...`)
     - Add `imagePullSecrets` and optional `global.imagePullSecrets`
     - Keep secrets external (`existingSecret`/`secretRef` patterns)
     - Add `podLabels`, `podAnnotations`, resource knobs, scheduling knobs
     - Keep optional capabilities disabled by default unless user requests otherwise

4. Apply template hardening
   - Add standard labels via helper templates:
     - `helm.sh/chart`
     - `app.kubernetes.io/name`
     - `app.kubernetes.io/instance`
     - `app.kubernetes.io/version`
     - `app.kubernetes.io/managed-by`
     - optional `app.kubernetes.io/part-of`
   - Add fail-fast checks for required values using `fail` (or `required` where appropriate).
   - Before changing `securityContext`, inspect existing manifests, values, Dockerfile references, init containers, volume mounts, and documented runtime requirements. Prefer configurable secure defaults over hard-coded settings that may break the workload.
   - Security defaults (unless incompatible with app runtime):
     - `runAsNonRoot: true`
     - `allowPrivilegeEscalation: false`
     - `capabilities.drop: ["ALL"]`
     - `seccompProfile.type: RuntimeDefault`
     - `readOnlyRootFilesystem: true` only when writable paths such as `/tmp`, cache directories, and log directories are backed by `emptyDir` or documented external volumes
   - RBAC:
     - Use separate `rbac` and `serviceAccount` values
     - Prefer `rbac.create: true`
     - Prefer `serviceAccount.create: true`
     - Prefer `serviceAccount.name: ""`
     - Prefer `serviceAccount.annotations: {}`
     - Prefer `serviceAccount.automountServiceAccountToken: false` unless the workload needs Kubernetes API access
     - Grant least-privilege Roles/RoleBindings only when the chart needs Kubernetes API access
   - Service account token:
     - Disable `automountServiceAccountToken` when workload does not need Kubernetes API.
   - Reliability:
     - split readiness and liveness (`/readyz` vs `/healthz`)
     - add `startupProbe` for slow starts (optional)
     - set rollout strategy and graceful shutdown (`preStop`, `terminationGracePeriodSeconds`)
   - HA options (optional):
     - `PodDisruptionBudget`
     - `topologySpreadConstraints` or anti-affinity
     - `NetworkPolicy` for ingress restriction
   - Observability options (optional):
     - `ServiceMonitor` rendered only when CRD exists
     - fail early when optional CRD-gated features are enabled without CRDs

5. Validate charts
   - Resolve chart dependencies before lint/render when `Chart.yaml` declares dependencies:
     - `helm dependency build <chart-dir>` when `Chart.lock` exists
     - `helm dependency update <chart-dir>` when there is no lock file
     - Inspect generated `Chart.lock` and `charts/` changes before keeping them
   - Run:
     - `helm lint <chart-dir> --strict` when warnings should block the change
     - `helm lint <chart-dir> --with-subcharts` when subcharts are in scope
     - `helm template smoke <chart-dir> --debug >/dev/null`
     - `helm template smoke <chart-dir> --kube-version <supported-version> >/dev/null` when supported Kubernetes versions are known
     - default and key option combinations
   - Validate optional features by simulating CRDs where needed:
     - `--api-versions cert-manager.io/v1/Certificate`
     - `--api-versions monitoring.coreos.com/v1/ServiceMonitor`
   - If the repository already uses `kubeconform`, `kubeval`, `datree`, chart-testing (`ct`), `helm-unittest`, snapshot tests, or policy-as-code checks, use the existing project toolchain instead of introducing a new validator.
   - If repository uses golden manifests, run snapshot verification and update only when changes are intentional.
   - If chart CI workflows are added, prefix workflow/job check names with the
     project or chart name (for example `sample-chart-render-snapshot-tests`)
     to avoid ambiguous status checks in monorepos.

6. Document results and usage
   - Update chart README with:
     - required secrets and dependencies
     - minimal install
     - production install options
     - optional features and defaults
     - troubleshooting/verify commands
   - If CI is added/changed, document trigger rules and local equivalents.

## Validation Commands

Use these defaults unless repository conventions differ:

```bash
CHART_DIR=path/to/chart

if grep -q '^dependencies:' "$CHART_DIR/Chart.yaml"; then
  if [ -f "$CHART_DIR/Chart.lock" ]; then
    helm dependency build "$CHART_DIR"
  else
    helm dependency update "$CHART_DIR"
  fi
fi

helm lint "$CHART_DIR" --strict
helm template smoke "$CHART_DIR" --debug >/dev/null
```

Optional feature smoke checks:

```bash
helm template smoke <chart-dir> --set hpa.enabled=true >/dev/null
helm template smoke <chart-dir> --set tls.enabled=true --set tls.certManager.enabled=true \
  --api-versions cert-manager.io/v1/Certificate >/dev/null
helm template smoke <chart-dir> --set monitoring.serviceMonitor.enabled=true \
  --api-versions monitoring.coreos.com/v1/ServiceMonitor >/dev/null
```

Supported Kubernetes version check:

```bash
helm template smoke <chart-dir> --kube-version <supported-version> >/dev/null
```

Optional repeatable default path: `scripts/validate-chart.sh <chart-dir>`. Read it before executing, follow user and repository script-execution rules, and mirror its command sequence manually when direct execution is not appropriate. It resolves dependencies, runs strict lint, runs a default debug render, and treats the sample ServiceMonitor render as non-fatal unless the chart declares that feature as supported.

## Output Contract

When using this skill, provide:

1. Findings first (by severity) with file references.
2. Exact implemented changes (files and rationale).
3. Validation results (`helm lint`, `helm template`, and CI checks if applicable).
4. Explicit list of deferred items and why they were deferred.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Guardrails

- Never put real secrets in `values.yaml`, templates, tests, or docs.
- Default optional integrations to disabled.
- Do not add legacy aliases or compatibility wrappers unless the user explicitly requests them; call out breaking values/schema changes clearly.
- Do not assume CRDs exist; gate and fail clearly if required features are enabled.
- Do not force chart `version` to match app `appVersion`; bump each on its own lifecycle.

## References

- For detailed checklist and decision criteria, read:
  - `references/best-practices-checklist.md`
