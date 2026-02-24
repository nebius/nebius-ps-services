---
name: helmchart
description: Apply Helm chart best practices and validate chart structure, values, and template quality. Use when users ask to create, review, harden, refactor, lint, or standardize Helm charts (Chart.yaml, values.yaml, templates/, NOTES.txt, schema, and chart CI tests).
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
     - SemVer `version` and `appVersion`
     - Keep `version` (chart packaging) independent from app SemVer
     - Keep `appVersion` aligned with the default app/image SemVer
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
   - Security defaults (unless incompatible with app runtime):
     - `runAsNonRoot: true`
     - `allowPrivilegeEscalation: false`
     - `capabilities.drop: ["ALL"]`
     - `seccompProfile.type: RuntimeDefault`
     - `readOnlyRootFilesystem: true` plus writable tmp volume when needed
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
   - Run:
     - `helm lint <chart>`
     - `helm template` default and key option combinations
   - Validate optional features by simulating CRDs where needed:
     - `--api-versions cert-manager.io/v1/Certificate`
     - `--api-versions monitoring.coreos.com/v1/ServiceMonitor`
   - If repository uses chart-testing (`ct`), run `ct lint`.
   - If repository uses golden manifests, run snapshot verification and update only when changes are intentional.

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
helm lint <chart-dir>
helm template smoke <chart-dir> >/dev/null
```

Optional feature smoke checks:

```bash
helm template smoke <chart-dir> --set hpa.enabled=true >/dev/null
helm template smoke <chart-dir> --set tls.enabled=true --set tls.certManager.enabled=true \
  --api-versions cert-manager.io/v1/Certificate >/dev/null
helm template smoke <chart-dir> --set monitoring.serviceMonitor.enabled=true \
  --api-versions monitoring.coreos.com/v1/ServiceMonitor >/dev/null
```

## Output Contract

When using this skill, provide:

1. Findings first (by severity) with file references.
2. Exact implemented changes (files and rationale).
3. Validation results (`helm lint`, `helm template`, and CI checks if applicable).
4. Explicit list of deferred items and why they were deferred.

## Guardrails

- Never put real secrets in `values.yaml`, templates, tests, or docs.
- Default optional integrations to disabled.
- Prefer additive, backward-compatible changes unless user explicitly requests breaking refactors.
- Do not assume CRDs exist; gate and fail clearly if required features are enabled.
- Do not force chart `version` to match app `appVersion`; bump each on its own lifecycle.

## References

- For detailed checklist and decision criteria, read:
  - `references/best-practices-checklist.md`
