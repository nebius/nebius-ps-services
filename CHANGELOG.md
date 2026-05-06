# Changelog

All notable changes to this repository will be documented in this file.

## [Unreleased]

- nebius-cxcli: replaced the old MysteryBox webhook bridge path with native
  External Secrets Operator `nebiusmysterybox` resources, including runtime
  Subject Credentials Secret creation and `mysterybox.payload-viewer`
  role-aligned auth handling.
- nebius-cxcli: made the bundled `mk8s` baseline CPU node count explicit in
  `component_sources.yaml` and generated `config.yaml` files via
  `inputs.cpu_nodes_count: 2`, instead of relying on a hidden Terraform module
  default.
- platform-infra/mk8s: removed the internal `cpu_nodes_count = 2` module
  default so direct consumers must choose the baseline CPU node-group size
  explicitly.
- platform-infra/mysterybox: changed the greenfield module contract from one
  optional `version` per secret to a `versions` map, with runtime payload
  values keyed by secret id, version id, and payload key.

### Added

- Added the `align` Codex skill under `skills/` for end-to-end project review
  and repair passes that inspect a codebase broadly before editing, then fix
  inconsistencies across implementation, tests, CI, CLI/help output,
  documentation, and formatting instead of only reporting them.
- Added the `attach-ubuntu` Codex skill with a Bash helper that creates or
  reuses a per-project Ubuntu container, mounts the current project at
  `/workdir`, updates VS Code attached-container defaults, bootstraps Ubuntu
  build tools and Python project dependencies inside the container, isolates
  them in a container-specific virtual environment, preserves Git metadata for
  monorepo subprojects, and best-effort opens a new Dev Containers window for
  the running container.
- Added the `create-pr` Codex skill under `skills/` for branch-safe GitHub PR
  creation: it detects the default branch, creates a feature branch only when
  work is still on that branch, reuses an existing feature branch otherwise,
  avoids duplicate PRs for the same head branch, and returns the PR number plus
  URL.
- Added the `onboard-nbs-cxcli` Codex skill under `skills/` as the central
  onboarding guide for Nebius Terraform modules that need to be wired into
  `services/nebius-cxcli`, including the catalog-first workflow and the
  optional code-owned layers for wizard/provider, runtime validation, status
  polling, and cluster handoff behavior.
- Added the `review-pr` Codex skill under `skills/` for GitHub-backed PR
  review and merge-readiness work: it inspects the PR against its base branch,
  fixes safe issues directly on the PR branch, attempts straightforward
  conflict resolution, reruns focused validation, and reports whether the PR is
  ready to merge.
- Added `nebius-cxcli component list`, `component add`, and `component remove`
  as the day-2 config-editing workflow for existing project `config.yaml`
  files, with interactive infra/apps selection and non-interactive CLI usage.
- Added catalog-driven Nebius API status watchers for deploy/apply so enabled
  Terraform modules can declare which Nebius SDK poller to run without baking
  module-specific status logic into the CLI command path.

### Changed

- Tightened the `helmchart` Codex skill with more accurate `appVersion`
  guidance, dependency-aware strict validation, safer securityContext and RBAC
  mutation rules, trigger-scope metadata, a reusable chart validation helper,
  and lightweight trigger eval prompts.
- Removed the custom MysteryBox ESO bridge service, bridge Helm chart, chart
  snapshots, and bridge image/chart workflows. `nebius-cxcli` now uses External
  Secrets Operator's native `nebiusmysterybox` provider, renders managed
  `ClusterSecretStore`/`ExternalSecret` objects through the `external-secrets`
  HelmRelease, requires `mbsec-...` MysteryBox secret IDs, and keeps Nebius
  service-account credentials as runtime-only Kubernetes Secrets.
- Clarified the `align` Codex skill guidance and metadata so alignment passes
  operate as cautious senior code-review style sweeps: audit-first,
  evidence-driven, wiring-aware, conservative around public contracts and
  business logic, and still focused on fixing verified low-risk gaps across
  code, tests, docs, workflows, CLI/help output, config, and applicable
  project skills.
- Updated the `skills/create-pr` guidance and metadata so Codex now treats any
  explicit user-supplied PR title as authoritative instead of inferring a
  generic preparation-style title from the branch name, and expanded
  `skills/README.md` with a copy-pastable custom-title example.
- Refined the new `create-pr` and `review-pr` skills under `skills/` to follow
  stronger Git and GitHub PR best practices: `create-pr` now explicitly
  refreshes the base-branch context, avoids opening PRs from a dirty tree
  alone, and prefers draft PRs for incomplete work, while `review-pr` now
  distinguishes safe local rebases from shared-branch cases that should use
  non-destructive updates, preserves unresolved reviewer concerns as explicit
  blockers, and routes selectively to sibling skills such as `align`,
  `github-workflows`, `helmchart`, `python-project`, `shell-scripting`,
  `linter`, `nebius`, `onboard-nbs-cxcli`, `terraform`, and the publish skills
  based on the PR surface.
- Expanded `skills/README.md` with explicit Codex chat prompt examples,
  including copy-pastable `create-pr` and `review-pr` usage plus the GitHub
  prerequisites those skills expect before they can act.
- Added a canonical Helm chart publication flow for `helm-charts/nccl-test`
  with chart-local `CHANGELOG.md`, `publish-helm.sh` prep/publish helper, a
  simplified tag-driven Nebius OCI publish workflow, anonymous public-pull
  verification, and a matching `publish-helm` skill under `skills/`.
- Hardened `helm-charts/nccl-test` by aligning the chart `appVersion` with the
  default NCCL image, merging local and global image pull secrets for both pod
  types, tightening Helm values schema validation, and expanding the chart
  README plus chart-local ignore rules.
- Expanded the repo-level Dependabot auto-merge policy so Dependabot-authored
  semver `uv` and `pip` dependency bumps can be auto-approved and auto-merged
  when every changed file is limited to Python dependency manifests or
  lockfiles, while source-code edits and other non-dependency file changes
  remain ineligible.
- Expanded `nebius-cxcli validate-sources` so it now validates fast
  Terraform-module and Helm-chart source contracts in addition to catalog
  shape and source-address resolution, including module `versions.tf`
  requirements, child-module backend/provider hygiene, and materialized chart
  layout checks (`Chart.yaml`, `values.yaml`, `templates/`).
- Refactored `nebius-cxcli create` and component-selection reconciliation to
  share the same source-driven config update path, preserving existing values
  while allowing new components to be added or removed safely.
- Removed internal `enabled` gates from the new `managed-postgresql` and `sfs`
  Terraform modules so `config.yaml` plus the generated Terraform root remain
  the single source of truth for whether a component is deployed.
- Updated `nebius-cxcli` runtime prompting and strict validation so
  `managed-postgresql` requires an explicit `inputs.name` when enabled, even
  when Terraform module metadata alone would not mark that field required.
- Clarified the `nebius-cxcli component` help/docs surface so one command
  family explicitly covers both infra modules and app charts, and added
  success-path test coverage for app chart add/remove flows.
- Aligned `component add` source-catalog validation with `create` so both
  commands validate `component_sources.yaml` by default and expose
  `--no-validate-sources` as the explicit opt-out.
- Updated `component add` to reuse `create`-style Nebius tenant/project scope
  validation before provider-backed prompts, and improved the fallback warning
  text so provider source names and SDK-init failures are visible.
- Clarified `deploy` in the CLI/docs as an idempotent reconcile/apply command,
  not a create-only path, and recommended `terraform plan` as the non-mutating
  preview step before a live deploy.
- Extended the generated manifest deploy metadata with resolved infra status
  watcher specs and taught local deploy/apply to fall back to the active
  catalog when an older generated bundle predates that metadata.
- Clarified in the CLI docs/tests that deploy status watchers resolve their
  `parent_id` and resource name from the enabled component inputs in
  `config.yaml`, and updated the `nebius-cxcli` CI workflow to rerun on
  `platform-infra/modules/**` changes and validate the workflow YAML itself.
- Fixed Nebius deploy status polling for MSP PostgreSQL so in-progress clusters
  are discovered from the service-native `clusters[]` list response and render
  human-readable phase/state names instead of raw enum integers.
- Refactored the `object-storage` Terraform module to manage one bucket per
  module instance, aligned `nebius-cxcli` prompting/strict validation with the
  required `inputs.name` field, and added catalog-driven Nebius Storage bucket
  status polling during deploy/apply. CLI error output now also escapes
  bracketed component ids so strict-validation messages keep paths like
  `infra.components[object-storage].inputs.name`.
- Aligned Terraform module usage with `nebius-cxcli` by teaching the component
  wizard to accept YAML/JSON for complex module inputs, skipping noisy optional
  `{}`/`[]` defaults, failing early on known effectively-required module
  inputs such as `mk8s.cpu_nodes_*`, `ssh-jumphost.allowed_cidrs`, and
  `mysterybox.secrets`, and tightening module-level validation/docs for the
  new module set.
- Updated `platform-infra/README.md` to document the canonical CLI-friendly
  Terraform module pattern for Nebius modules, including no internal
  `enabled` toggles, stable identity inputs/outputs, YAML/JSON collection
  inputs, and the status/handoff expectations used by `nebius-cxcli`.
- Expanded `platform-infra/README.md` with a concrete new-module authoring
  checklist for `platform-infra/modules/*`, including required files/examples,
  validation steps, and the `component_sources.yaml` fields needed when a new
  Terraform module should be exposed through `nebius-cxcli`.
- Refactored `nebius-cxcli` component selection/render/runtime wiring so one
  instance config can enable the same component type multiple times, using
  unique `instance_id` values for add/remove/list, Terraform module naming,
  output bindings, generated manifests, deploy status watchers, and inventory
  resolution.
- Aligned `nebius-cxcli` on a project-scoped path contract so `create`,
  `discover`, help text, generated manifests, docs, and tests consistently use
  `projects/<client>--<tenant>/<project>/config.yaml` as the canonical
  customer-repo layout.
- Updated the `nebius-cxcli` design docs to use stable unnumbered sections,
  document `setup.py` in the source structure, and keep project-vs-component-
  instance terminology explicit.
- Updated the shared `python-project` skill to explain when a minimal
  compatibility `setup.py` shim still makes sense alongside `pyproject.toml`.
- Refined the interactive `create` and `component add` wizard exit contract so
  `q` can stop at any point without discarding the current config edit; the
  CLI now warns only when required fields remain unresolved and stays quiet
  when only optional fields are left at defaults.
- Interactive `create` now warns and asks for confirmation before reconciling
  an already-existing project config, so accidental reruns do not silently
  update the initial customer project scaffold.
- Interactive `create` now also prints an early notice when the deployments
  root already contains project configs, so operators know immediately that
  the target tree is not a net-new customer scaffold.
- Interactive `create` now reuses the single existing project config in a
  deployments root as the default prompt identity (`client_name`,
  `tenant_id`, `project_id`) when no explicit identity flags were provided, so
  reruns can advance through reconcile mode with Enter instead of retyping the
  same project identifiers.
- Interactive `create` now stops for an explicit continue/quit decision as
  soon as it detects that the deployments root already contains project
  configs, instead of waiting until after the project-identity prompts.
- The early existing-deployments-root guard in interactive `create` now
  defaults to continue so operators can press Enter through it, and the later
  exact-project reconcile confirmation now also defaults to continue.
- `create` now runs the non-strict `validate` pass against the resulting
  `config.yaml` by default and exposes `--no-validate-config` as the explicit
  opt-out, so the generated project config is checked immediately after write.
- Fixed `create` so leaving the optional notifications email blank now writes
  `client_info.notifications.email_enabled: false` with a null recipient,
  matching the documented default-disabled email contract and prompt wording.
- Refactored the app-chart wizard so live Helm chart defaults stay implicit in
  the chart instead of being copied into `config.yaml`; prompts still surface
  those defaults dynamically, but only explicit overrides are persisted and
  redundant old default copies are cleaned out on rewrite.
- Refactored the interactive component field wizard so `create` and
  `component add` offer all discoverable required and optional Terraform/Helm
  fields for newly selected components, enforce required values immediately,
  and keep optional blanks implicit instead of writing `null` or copied module
 /chart defaults into `config.yaml`.
- Improved provider-fallback wizard messaging so when live Nebius option
  lookups are unavailable, the CLI now prints a field-specific warning before
  the next prompt and explains whether the manual prompt is required or can be
  skipped with Enter.
- Cleaned up interactive complex-value prompts so empty optional YAML/JSON
  defaults (`{}` / `[]`) no longer render as awkward inline prompt defaults;
  the wizard now explains that blank input keeps the current empty map/list.
- Fixed Terraform fallback variable introspection so multiline defaults from
  `variables.tf` files, including map/object literals such as
  `mk8s.gpu_driver_preset_map`, are parsed as full values instead of being
  truncated to `'{'` during interactive wizard prompts.
- Clarified the CLI idempotency contract in the README/design docs so
  read-only and reconcile commands are explicitly distinguished from
  intentionally non-idempotent additive, rotation, and delivery actions such
  as `component add`, `auth --recreate`, and `email`.
