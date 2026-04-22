# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Fixed the new NCCL transport-selection path end to end: the shared
  `nccl-test` chart now renders its Socket/TCPIP and RDMA `mpirun` env wiring
  correctly, cxcli now derives NCCL worker GPU count from the resolved MK8s
  shape while sizing worker CPU/memory from live scheduler headroom and
  pinning the launcher onto non-GPU nodes when available, so Ethernet-only
  1-GPU clusters stay schedulable instead of leaking the 8-GPU worker profile
  or spending GPU-node headroom on the launcher. The transport contract stays
  covered by a Helm-backed render regression when Helm is available, and
  GitHub Actions now triggers on `helm-charts/nccl-test` / `services/nccl-test`
  changes and runs explicit socket/RDMA chart smoke renders so
  transport-specific template bugs fail in CI instead of surfacing only during
  live `deploy`.
- NCCL deploy validation now runs for GPU-enabled MK8s clusters on both
  Ethernet-only and GPU-cluster / InfiniBand shapes. `deploy` auto-selects the
  NCCL transport from the resolved MK8s context, using Socket/TCPIP on
  Ethernet-only shapes and RDMA on GPU-cluster shapes, while enforcing the
  configured bus-bandwidth threshold only on the RDMA path. The MK8s wizard now
  exposes NCCL enable/max-nodes controls for all GPU-enabled shapes and hides
  only the RDMA-specific threshold field until the current shape is actually on
  the GPU-cluster / fabric path.
- Removed the hardcoded MK8s InfiniBand fabric table from the wizard/provider
  path. For cluster-capable GPU presets, `inputs.infiniband_fabric` choices now
  come from live Nebius Capacity Dashboard fabric rows, while live preset
  `allow_gpu_clustering` metadata remains the gate that decides whether a shape
  is actually GPU-cluster / RDMA-capable. Runtime validation now also rejects a
  configured fabric that does not match the live Capacity Dashboard rows for
  the selected shape when those rows are available.
- Fixed the first-party NCCL validation race and clarified the surrounding MK8s
  GPU contract: the `nccl-test` launcher now waits for each worker pod's main
  `nccl` container to become Ready before starting `mpirun`, docs now explain
  that `GPU stack readiness` already scans all Ready GPU nodes while
  `average bus bandwidth` is NCCL's normalized collective metric rather than a
  raw switch-port speed, and the driverful `nebius_image` path is documented as
  keeping Network Operator optional outside the shapes where Nebius requires it.
- Clarified MK8s GPU validation semantics and cleanup: the first deploy-time
  gate is now labeled `GPU stack readiness` in operator-facing output because
  it covers GPU Operator plus Network Operator / `NicClusterPolicy` when the
  selected shape requires the network stack, and the docs now say explicitly
  that cxcli keeps dedicated validation namespaces while deleting transient
  validation pods, transient NCCL `MPIJob` resources, and any transient
  Training Operator install after each run.
- Quota assessment now prefers operator auth such as `NEBIUS_IAM_TOKEN` or a
  Nebius CLI profile before falling back to the auto-bootstrapped runtime
  project service account. That keeps tenant-scope quota and Capacity
  Dashboard reads working during `deploy` / `quota-check` / `render` reruns
  when the operator has tenant-visible credentials, instead of needlessly
  warning on `PERMISSION_DENIED` from the project-scoped runtime identity.
- Fixed generated-bundle MK8s quota preflight for reruns: `deploy` and
  `validate-generated` now initialize the rendered backend, read the current
  Terraform state, and subtract MK8s quota already managed by that bundle
  before comparing the desired bundle against live Nebius quota/capacity. That
  keeps unchanged reruns of an existing cluster idempotent instead of failing
  like fresh creates, while still failing fast when the rerun would add real
  net-new capacity such as more nodes or a larger GPU shape.
- Flattened the deploy-report refresh CLI to one canonical command:
  `nebius-cxcli report <config.yaml>` now directly rewrites
  `generated/inventory/deploy-report.md`, replacing the confusing one-off
  `report write` subcommand. Help text, docs, and follow-up guidance now name
  the exact artifact path so operators can immediately see what the command is
  for.
- Added a new early design-doc section, `How Flux Works`, to explain the shared
  Flux controller model, the difference between `HelmRepository` vs
  `HelmRelease` status, what `image-automation-controller` is, how local
  `deploy` / `flux apply` differ from `flux bootstrap`, and which `kubectl`
  commands operators can use to check workload-release health vs GitOps
  bootstrap state.
- Tightened the local Flux success note for the "only source objects still pending"
  edge case: the CLI now says plainly that rendered `HelmRelease` workloads are
  already `Ready`, skips the remaining source-object wait, and prints
  `kubectl get helmreleases.helm.toolkit.fluxcd.io -A` as the direct follow-up
  command for operators who want to verify installed release health.
- Fixed generated-bundle Terraform output lookup for day-2 app commands:
  `terraform output -raw/-json` now initializes the rendered backend before
  reading outputs, so `flux apply`, `flux bootstrap`, built-in MK8s cluster
  handoff, and other Terraform-output-driven generated-bundle paths work on a
  freshly rendered `generated/infra` directory instead of failing with
  Terraform's "Backend initialization required" error. Flux API discovery now
  checks resource types cluster-wide instead of requiring app target
  namespaces to exist before `flux apply` creates them.
- Fixed MK8s GPU app-value materialization for persisted operator config:
  MK8s GPU policy-managed chart-value paths are now authoritative instead of
  preserve-existing. On `create`, `component add`, and `render`, cxcli rewrites
  the currently applicable policy paths from the catalog and clears stale
  no-longer-applicable policy paths. That prevents `render` / `deploy` from
  carrying forward malformed or outdated Helm values that make Flux fail the
  `nvidia-network-operator` install with Kubernetes validation errors.
- Fixed generated-bundle MK8s resource-name preflight to treat Nebius
  `Request error NOT_FOUND: ...` responses as the expected "resource absent"
  case. That keeps `deploy`, `validate-generated`, and `terraform plan/apply`
  from falsely failing after operators delete a stale live MK8s or GPU cluster,
  while still failing fast on real live-name collisions that would make
  Terraform hit `AlreadyExists`.
- Deploy-time generated-bundle validation now fails fast on live MK8s name
  collisions before Terraform apply: after backend init, cxcli checks whether a
  bundled MK8s cluster name or its derived GPU-cluster name already exists live
  in the target project while not being tracked in the current Terraform state.
  That turns late Terraform `AlreadyExists` failures into targeted preflight
  guidance telling operators to delete the stale live resource, import it into
  state, or rename the cluster and rerender.
- Changed `create` to generate name-derived project folders instead of ID-derived folders: operators still enter `tenant_id` / `project_id`, but after those IDs are validated the starter config now lands under `<deployments-root>/<tenant-name>/<project-name>/config.yaml` using filesystem-safe slugs from the resolved Nebius tenant/project names. `config.yaml`, generated manifests, GitHub environment names, deploy-report email identity, and other runtime surfaces still use `tenant_id` / `project_id` as the authority rather than inferring identity back from the folder names. The CLI now fails fast on name-based folder collisions so one project cannot overwrite another just because their normalized names would map to the same path, and the docs/tests/examples were updated to treat `<tenant-folder>/<project-folder>` as the canonical path shape for config-based commands.
- Reworked `quota-request` around the correct quota object model: live
  insufficiency detection still reads `QuotaAllowance`, but request creation is
  now treated as a separate `QuotaRequest` path. Internal Nebius operator
  environments on the Nebius internal network can auto-submit through the
  internal request surface, while external/public environments fall back
  cleanly to exact manual quota-request guidance instead of mutating quota
  allowances directly.
- Clarified the top-level README install contract: prerequisites and install
  steps now live near the top of the document, the command-specific local-tool
  requirements are called out explicitly, and the Helm wording now makes clear
  that Helm is needed for source/chart validation paths rather than as a
  blanket prerequisite for normal render/deploy use.
- Fixed bundled MK8s NCCL default hydration when `helm` is unavailable:
  local/unit-test resolution now falls back to the checked-in
  `helm-charts/nccl-test/values.yaml`, so the validation spec still keeps the
  first-party `chart_values.image.*` and benchmark defaults instead of
  degrading to an incomplete override-only payload.
- Removed the public `validate --strict` split: `validate` now always runs the
  deployment-readiness stack, `validate-generated` now reuses the same strict
  generated-bundle preflight as `deploy`, and the warning-only non-strict path
  remains internal to `create` so bootstrap/edit flows still continue through
  quota shortages until operators explicitly request quota.
- Aligned `validate-generated --help` and the command reference text with the
  actual generated-bundle contract: the help surface now calls out readiness,
  manifest validation, and optional portability explicitly instead of sounding
  like a generic artifact-only check.
- Improved `quota-request` manual fallback output: when automatic request
  submission is unavailable for the current identity or environment, the
  console-follow-up block now prints the minimum target limit and minimum
  increase to request for each confirmed shortage instead of listing only the
  quota names.
- Centralized GPU quota checks on the live Nebius Capacity Dashboard
  `resource-advice` surface: GPU quota sufficiency now resolves against the
  exact platform/region/preset/fabric shape instead of mixing regular quota
  allowances with a separate Capacity Block Group overlay, and `quota-check`
  / `validate` / `create` / `render` / deploy-time preflight now all share
  that same GPU path.
- Improved live GPU wizard guidance across bundled infra flows: GPU preset
  prompts now annotate/rank supported GPU shapes with live Nebius Capacity
  Dashboard `resource-advice` availability when tenant/region context is
  available, optional InfiniBand fabric prompts now annotate the exact
  platform+preset fabrics with live on-demand/reserved availability and
  highlight the recommended default without forcing the field to be set, and
  `create` quota warnings now print the exact `quota-request <config.yaml>`
  follow-up command instead of stopping the config workflow.
- Aligned shared GPU interconnect guidance across MK8s and VM wizard flows:
  single-GPU shapes are labeled as Ethernet-only testing/dev options, while
  clusterable multi-GPU shapes are labeled as the InfiniBand /
  GPUDirect-RDMA path. Fabric-scoped Capacity Dashboard rows for single-GPU
  shapes are now treated as availability-ranking input only, and stale VM
  fabric values are cleared during interactive edits when the selected GPU
  shape no longer supports clustering.
- Tightened GPU-cluster contract alignment with the public Nebius Compute VM
  types guidance: VM GPU-cluster validation no longer hardcodes an `8gpu-*`
  name check when live preset metadata is available, and MK8s deploy
  validation now warns when operators force NCCL onto Ethernet-only /
  non-cluster GPU shapes instead of silently pretending that configuration is
  representative of an InfiniBand training environment.
- Fixed MK8s deploy status fail-fast handling so `deploy` no longer aborts
  immediately on stale old node-group error events from a previous failed run
  when Terraform is about to replace that failed group. Fresh terminal API
  errors from the current run still abort early.
- Fixed `generated/inventory/deploy-report.md` formatting so render/report
  output no longer ends with duplicate blank lines when deploy validations are
  present, keeping the generated Markdown clean for linting in customer repos.
- Changed interactive `create` so `tenant_id` / `project_id` no longer
  default from an existing project under the deployments root. `create`
  now assumes a new target unless you explicitly pass or type an existing
  tenant/project, and only then warns before overwriting that resolved folder.
- Merged the human-readable inventory and deploy-validation markdown outputs
  into one canonical `generated/inventory/deploy-report.md`. It now combines
  `Infra`, `Apps`, and `Validations`, `report` refreshes that single
  file, `email` sends that same file, deploy-time validations still keep their
  per-validation JSON detail reports, and stale markdown/report artifacts are
  cleared before each deploy run so skipped or failed runs do not leave
  misleading old summaries behind.
- Tightened the project-level runtime entrypoints to one canonical target:
  `deploy`, `destroy`, `report`, and `email` now accept only
  `config.yaml`, resolve sibling `generated/` automatically, and reject direct
  `generated/` targets instead of keeping a backward-compatibility dual path.
  The generated manifest and rendered inventory artifacts remain the
  authoritative runtime contract after render, so post-render source edits do
  not silently change what gets applied, destroyed, written, or emailed.
- Clarified `validate` and `quota-check` help/docs wording so the command
  surface now matches the actual live quota-plus-capacity checks already used
  by runtime validation.
- Tightened `create` overwrite semantics so "from scratch" now includes
  `client_info`: once an existing resolved project folder is confirmed
  for overwrite, the client name / region / notification prompts restart from
  the normal create defaults instead of reusing the old config values.
- Fixed the MK8s GPU validation wizard to hide the
  `deploy.validations.mk8s_gpu.health_checker.enabled` toggle unless the active
  catalog actually exposes an apps component with
  `cli.mk8s_gpu_policy.role: health_checker`, so bundled catalogs no longer
  present an impossible health-checker prompt during `create` / `component add`.
- Fixed component-level wizard phase control flow so answering `n` to
  `Configure '<component>' component fields now?` skips only that component and
  continues with the remaining selected components, while `q` still stops the
  wizard. This fixes the MK8s GPU app case where skipping
  `nvidia-network-operator` previously prevented the later
  `nvidia-gpu-operator` prompt from appearing at all.
- Tightened the MK8s GPU health-checker contract so the bundled NVIDIA path
  treats it strictly as a custom app-policy hook instead of a built-in deploy
  validation: bundled project defaults now omit `health_checker` unless the
  active catalog actually supplies a compatible app, and `deploy
  --skip-validation` no longer advertises a nonexistent `health-checker`
  built-in validation kind.
- Fixed the Nebius `gpu_stack_source: nebius_image` MK8s path so the bundled
  catalog now renders the missing driverful-node policy: GPU Operator keeps
  host GPU-driver and NVIDIA Container Toolkit management disabled, and the
  bundled operator path now suppresses GPU Operator's NFD whenever Network
  Operator owns the networking stack so only one NFD instance is deployed.
  Network Operator enables NFD plus Mellanox NodeFeatureRules and adds a Helm
  post-render patch that exposes `rdma/shared_device` on driverful InfiniBand
  nodes without deploying the OFED driver container.
- Fixed the manual MK8s GPU-cluster / InfiniBand path so the bundled Network
  Operator render also patches `NicClusterPolicy` with `rdma/shared_device`
  instead of relying on the chart default CR, which only handled OFED. Manual
  operator-managed InfiniBand nodes now line up with the same scheduler-visible
  RDMA contract that deploy-time readiness validation already expects.
- Refactored the bundled MK8s GPU app-policy catalog so reusable driverful NFD
  overlays and `NicClusterPolicy` RDMA patch bodies can be named once under
  `cli.mk8s_gpu_policy.default_sets` / `post_render_patch_sets` and referenced
  from multiple rules. This keeps the Network Operator RDMA plugin tag and
  selector details catalog-owned without repeating the same patch inline across
  multiple `component_sources.yaml` rules.
- Fixed the MK8s GPU allocatable-resource filter to parse Kubernetes extended
  resource prefixes explicitly instead of matching the literal
  `nvidia.com/` prefix with a raw string prefix check, avoiding a false-positive
  CodeQL URL-sanitization warning without changing the GPU/RDMA readiness
  behavior.
- Clarified and locked in the layered MK8s GPU validation contract: source
  comments, README/design docs, and regression tests now explicitly treat
  `operator_readiness`, `gpu_visibility`, and `nccl` as a cheapest-to-most-
  expensive chain with distinct responsibilities rather than overlapping
  duplicate checks.
- Ignored local coverage data files and packaged chart archives in the service
  repo `.gitignore`, and clarified that the managed customer deployments
  `.gitignore` stays intentionally narrow to generated Terraform runtime files
  and tfvars instead of acting like a generic developer ignore file.
- Exposed bundled MK8s GPU validation controls as a project-facing deploy
  contract under `deploy.validations.mk8s_gpu.*`, so these CLI deploy checks
  no longer masquerade as Terraform inputs. The wizard still surfaces the same
  toggles from catalog defaults, but the resulting values now persist in
  `config.yaml` as deploy settings, and local `deploy` also supports one-run
  `--skip-validations` / `--skip-validation <kind>` overrides.
- Removed the temporary backward-compatibility shims from that MK8s GPU
  validation contract: `infra.components[].inputs.gpu_validation_overrides`
  now fails fast instead of being migrated, and local `deploy` now requires
  generated-manifest `deploy.validations` metadata instead of recomputing GPU
  validation specs from older bundles at runtime.
- Tightened the interactive MK8s GPU app flow: when the infra prompts turn on
  a GPU shape that requires `nvidia-gpu-operator` or
  `nvidia-network-operator`, the wizard now auto-enables those app rows before
  the app phase starts so the same `create` / `component add` pass can still
  show their prompts instead of only materializing them later in `config.yaml`.
- Simplified the bundled `mk8s` source catalog by removing the one-off raw
  `wizard:` block for GPU validation helper defaults. cxcli now derives those
  virtual prompt defaults directly from `components.infra.mk8s.cli.gpu.validations`
  during source parsing, so the catalog keeps one source of truth while the
  interactive wizard behavior stays unchanged.
- Removed the now-unused YAML anchors from the bundled MK8s
  `cli.gpu.validations` defaults after the wizard-helper refactor, so the
  catalog no longer carries dead alias syntax.
- Clarified the MK8s boot-disk wizard wording for
  `NETWORK_SSD_NON_REPLICATED`: it now describes the disk as the lowest-cost
  high-performance SSD-backed option, not the cheapest disk overall.
- Tightened MK8s GPU operator readiness around live cluster behavior: the
  readiness report now requires allocatable GPUs on Ready nodes instead of
  assuming manual `nvidia.com/gpu.deploy.*` labels, which matches the current
  Nebius-image path where GPUs can be allocatable even while the upstream GPU
  Operator `ClusterPolicy` still reports `NoGPUNodes`.
- Tightened MK8s GPU-cluster / InfiniBand readiness further so `deploy` no
  longer treats a fabric-enabled cluster as ready just because `ClusterPolicy`
  and `NicClusterPolicy` report `ready`: the saved operator-readiness report
  now also records `NicClusterPolicy.status.appliedStates`, checks that Ready
  GPU nodes advertise scheduler-visible RDMA-style allocatable resources (for
  example `rdma/shared_device`), and fails fast when the control-plane objects
  are green but pod-facing RDMA exposure is still missing.
- Simplified the live MK8s operator-readiness polling loop: `ClusterPolicy`
  and `NicClusterPolicy` remain the primary control-plane signals, allocatable
  GPUs on Ready nodes remain the GPU data-plane gate, daemonset rollout
  summaries are now collected once for the saved report instead of being
  polled on every pass, and local `deploy` now treats manifest
  `deploy.validations` as a required part of the generated-bundle contract
  instead of recomputing runtime-derived GPU validation specs from older
  bundles.
- Refined the bundled GPU Visibility reporting contract: the validation still
  uses a sampled CUDA workload as the authoritative pass/fail gate, but its
  saved report now also captures the Ready GPU nodes' allocatable
  device-plugin resources so operators can inspect `nvidia.com/gpu` and any
  RDMA-style resource keys without mistaking raw `allocatable` output for a
  full runtime proof.
- Fixed bundled MK8s GPU Operator deploys on Nebius-managed GPU images by
  also disabling the chart's Nebius `NVIDIADriver` CRD path in the rendered
  Helm values, avoiding the live `gpu-operator@v25.10.0` Flux install failure
  on `templates/nvidiadriver_nebius_patch.yaml`.

- Clarified the source-config validation contract: `validate --strict` help now
  explicitly calls out strict readiness, MK8s preflight, and fail-fast live
  quota/capacity checks, and `component add` / `component remove` now point
  operators at the same `validate`, optional `validate --strict`, then
  `render` day-2 loop already used after `create`.
- Hardened `deploy <generated-dir>` with an explicit generated-bundle
  preflight before Terraform apply: strict readiness checks against the
  manifest runtime config, MK8s network preflight, live Nebius
  quota/capacity validation, Terraform validation for `generated/infra`, and
  rendered Flux manifest validation when apps are enabled now all fail fast
  inside `deploy` itself instead of relying on operators to run separate
  commands first.
- Changed plain `validate <config.yaml>` to also run the live Nebius
  quota/capacity assessment in warning mode, so operators now see confirmed
  shortages before `deploy` while `validate --strict` keeps the fail-on-
  insufficiency readiness gate.
- Added `quota-request <config.yaml>`, which reuses the existing live quota
  assessment and plans direct tenant/project quota allowance requests for the
  confirmed insufficient quota dimensions through the published Nebius quota
  API instead of requiring manual web-console entry; the CLI prints the target
  limits it plans to request, falls back cleanly to a manual Administration →
  Limits → Quotas follow-up when Nebius denies the direct API write, now also
  prints coverage-gap detail when nothing can be submitted, and points
  operators to the web console for submission or status tracking.
- Refactored bundled MK8s boot-disk defaulting so the catalog now owns
  ordered cxcli boot-disk rules under
  `components.infra.mk8s.cli.boot_disk_defaults.<cpu|gpu>`, keyed by resolved
  preset resources such as vCPU, RAM, and GPU count. `create`, `component
  add`, and runtime config loading now materialize explicit
  `cpu_nodes_boot_disk_*` / `gpu_nodes_boot_disk_*` values from the first
  matching rule for the selected shape, while unmodeled shapes still fall back
  to the heuristic. Guided disk-type prompts now show consistent Nebius
  price/performance labels for all three recommended SSD-backed choices and
  clarify that MK8s boot-disk encryption is not configurable from cxcli.
  High-performance SSD types still round to required 93 GiB multiples, regular
  `NETWORK_SSD` values stay exact GiB sizes, explicit first-class inputs or
  `template.boot_disk` overrides remain authoritative, and the quota
  estimator/request planner can now cover the common `compute.disk.size.*`
  MK8s shortages without waiting for a deploy-time failure. Public MK8s
  node-group `boot_disk` still exposes size/type only, so cxcli documents but
  does not attempt to toggle optional SSD NRD / SSD IO M3 encryption.
- Extended the live bundled infra quota assessment to also consider active
  tenant Capacity Block Groups for fabric-bound GPU requests, so matching CBG
  capacity can satisfy `compute.instance.gpu.*` and
  `compute.gpucluster.count` during `validate --strict`, `create`,
  `quota-check`, `render`, and deploy-time guard rails instead of emitting
  false zero-quota warnings.
- Refined generated-bundle destroy behavior so top-level `destroy` now skips
  separate Flux app deletion when the generated infra bundle destroys the
  handed-off MK8s cluster directly, while external-cluster app bundles still
  delete rendered Flux resources first. `destroy`, `terraform destroy`, and
  `flux destroy` now print path-specific confirmation warnings for the actual
  target they remove, and `flux destroy` / pre-destroy app teardown now skip
  cleanly with a note when the target cluster is reachable but Flux CRDs are
  already absent instead of surfacing raw `kubectl` resource-mapping errors.
- Tightened interactive wizard UX: flat Terraform module-input prompts now use
  `q` to revisit the previous prompt instead of only skipping ahead, while
  nested value/object prompts keep the existing branch-level backout behavior.
  The MK8s boot-disk wizard still documents the NRD / IO M3 encryption
  limitation in the README/design docs, but that note is no longer repeated in
  the live prompt banner or disk-type option labels.
- Removed hardcoded dollar figures from the MK8s boot-disk wizard labels so
  live CLI guidance does not drift as Nebius pricing changes. The README and
  design doc now point operators to the official Nebius disk-type and pricing
  pages for current values instead of restating specific amounts in the prompt
  text.
- Improved `validate` / `validate --strict` terminal output with one concise
  validated-scope list that separates `infra` and `apps` and shows their
  catalog groups such as `Compute`, `Storage`, `Platform`, or `Workloads`,
  so successful validation is more informative without adding another heavy
  inspection pass.
- Fixed bundled app runtime Helm resolution when an app id differs from the
  chart basename: dependency lookup, post-create source validation, live chart
  default pruning, and Flux rendering now keep using the catalog chart name
  (for example `network-operator` / `gpu-operator`) instead of incorrectly
  reconstructing refs from app ids such as `nvidia-network-operator`.
- Improved the bundled MK8s wizard default for `inputs.k8s_version`: the
  first live Nebius control-plane version is now auto-selected into the
  interactive flow instead of defaulting to an unset value.
- Refined interactive wizard exit behavior for field prompts: `q` now backs
  out one prompt-prefix level within the current component instead of aborting
  the whole wizard immediately, while `qq` preserves the full wizard stop path.
- Adjusted per-component field-phase defaults in the interactive wizard so
  infra components still default to `y`, while app chart field prompts now
  default to `n` because chart overrides are normally optional.
- Clarified the remaining B200-only NCCL MPI overlay contract in
  `component_sources.yaml`, README, and the design doc: the bundled
  `-mca coll ^hcoll` override stays catalog-owned because the official Nebius
  B200 NCCL example includes it while the H100/H200 example does not, and the
  docs now also point to NVIDIA HPC-X release notes / known issues that mark
  HCOLL unsupported on GB200/GB300 as additional nearby context.
- Tightened MK8s in-cluster deploy validation behavior so `deploy`, `flux apply`, and `flux bootstrap` no longer block on a generic all-nodes-ready pre-wait, MK8s GPU validations now emit live Kubernetes status instead of silently polling, local `deploy` keeps a continuous spinner alive across those validation phase transitions with non-TTY log fallback, and the bundled GPU Visibility/NCCL checks now bound their default node fan-out with catalog-owned `max_nodes` caps plus shorter default timeouts to keep deploy-time validation fast on large clusters.
- Simplified the bundled app-side MK8s GPU catalog contract: `components.apps.<id>.cli.mk8s_gpu_policy` now uses one conditional `rules` list where each rule can auto-enable the app and/or contribute conditional chart defaults, replacing the earlier split between `auto_enable` and `value_overrides` while keeping top-level app `defaults` as the unconditional chart-default layer.
- Added the published portable OCI source for the bundled `nccl-test` Helm chart in `component_sources.yaml`, so the NCCL validation chart now resolves through the same dual `source.local` / `source.portable` contract as the other bundled charts.
- Aligned the bundled NCCL validation image overrides with the first-party `services/nccl-test` release path, so `component_sources.yaml` now points at `cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test` SemVer tags instead of the legacy `nebius-benchmarks/nccl-tests` repository.
- Pinned the bundled NCCL chart/image contract to the current first-party release set: `component_sources.yaml` now keeps the portable chart source on `oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test --version 0.2.7`, the bundled MK8s GPU validation path consumes the runtime image `cr.<region>.nebius.cloud/<registry-short-id>/images/nccl-test:0.2.0` from the chart's own defaults, and release-catalog coverage now guards OCI chart refs from being rewritten back to legacy GitHub tree paths.
- Simplified the bundled MK8s GPU app catalog around live chart defaults and customer-facing reports: the shared NCCL image/tag plus deploy-time benchmark defaults are now sourced directly from `helm-charts/nccl-test/values.yaml`, only the B200-specific MPI overlay remains in `mk8s_gpu_policy.rules`, redundant operator values that already match the live NVIDIA chart defaults were dropped from `component_sources.yaml`, and the generated GPU validation reports now preserve readable field order while keeping only compact summaries plus failure-focused log excerpts.
- Fixed the remaining `nebius-cxcli-ci` wheel gate for local-only charts: branch CI now verifies that the built wheel bundles `component_sources.yaml` without forcing release-grade portable chart sources, while the tag/release workflow still runs the stricter portable `verify-wheel` / `verify-catalog` checks.
- Fixed `nebius-cxcli-ci` catalog validation for branch work: the normal CI workflow now runs `validate-sources component_sources.yaml` with source profile `local` so new in-repo Terraform modules and local-only Helm charts are validated against the checked-out branch, while the release workflow keeps the portable-profile validation for published wheel/catalog verification.
- Aligned the remaining strict-validation and docs surfaces with the current Helm/source contract: the MK8s GPU strict-validation coverage now enables `nvidia-gpu-operator` before asserting missing GPU shape fields, and the README/design examples now consistently show app charts under `source.portable` instead of the removed top-level `source.repo/chart/version` layout.
- Added a bundled `vm` infra component backed by `platform-infra/modules/vm`: the catalog now exposes guided project-subnet and live compute platform/preset selection, resolves `source_image_family` from the live Nebius public image inventory using component-local image preference ordering under `components.infra.vm.cli.image_preferences`, keeps the module boot-image contract explicit instead of hiding a hardcoded family default, preserves static public-IP mode choices plus optional GPU-cluster fabric guidance, and includes runtime validation/quota estimation for standalone Nebius VMs so the new module behaves like a first-class `nebius-cxcli` component instead of a raw custom Terraform source.
- Refactored the bundled MK8s GPU contract around the actual Nebius node-group model: `inputs.gpu_stack_source` and `inputs.gpu_stack_preset` now replace the earlier driver-centric terminology in the customer- and catalog-facing contracts, the MK8s module/docs now describe Nebius-managed `gpu_settings.drivers_preset` vs manual/operator-managed GPU stacks explicitly, and the NCCL path now renders a first-party `helm-charts/nccl-test` chart selected through the same Helm `source.portable` / `source.local` contract used by other bundled charts instead of assembling the raw `MPIJob` manifest in Python.
- Replaced the old MK8s GPU hardcoded profile split with component-local catalog policy: `component_sources.yaml` now keeps MK8s GPU image preferences and validations under `components.infra.mk8s.cli.gpu`, keeps GPU operator/network operator auto-enable rules and Helm value overrides on the operator app entries themselves under `components.apps.<id>.cli.mk8s_gpu_policy`, removes the unused standalone `nvidia-device-plugin` catalog entry, still materializes Nebius-image vs manual MK8s defaults from the live Nebius compatibility matrix, keeps the GPU Operator B300 driver pin in the catalog instead of Python, and still persists deploy-time GPU readiness/visibility/NCCL reports under `generated/inventory/`.
- Changed interactive `create` overwrite UX so it now resolves `tenant_id` / `project_id` before showing any overwrite warning: existing deployments roots no longer emit a root-wide pre-warning, and confirmation appears only when the chosen resolved project folder already exists.
- Changed the canonical project layout to match the two-level project hierarchy under the deployments root: project configs now live at `<deployments-root>/<tenant-folder>/<project-folder>/config.yaml`, and `create <deployments-root>` is a bootstrap/overwrite command instead of an existing-config reconcile path. Once that resolved project folder already exists, interactive reruns now require explicit overwrite confirmation, non-interactive reruns require `--force`, overwrite recreates only that one resolved project folder from scratch, client-info prompts restart from the normal create defaults, and infra/apps selections plus component values are rebuilt from the current create inputs instead of being merged from the old config; docs/help/tests were realigned to make `component list/add/remove` the default day-2 editing surface.
- Tightened the remaining help/docs wording around the project-folder layout so `create --help`, README, and the design doc consistently describe the canonical overwrite target and the generated customer workflow's canonical `<tenant-folder>/<project-folder>/generated/**` watch scope.
- Tightened the generated customer GitHub workflow trigger to the canonical two-level deployment layout under the deployments root: it now watches only `.../<tenant-folder>/<project-folder>/generated/**` paths instead of a broader recursive `generated/**` glob that could still match stale pre-refactor layouts.
- Extended catalog-driven Nebius fail-fast status monitoring beyond MK8s: bundled jump-host modules now declare live `nebius.compute.instance` watchers, bundled `mysterybox` now declares `nebius.mysterybox.secret` watchers that expand one component row into one watcher per configured secret name, supported watcher kinds now include compute instances and MysteryBox secrets, and the MSP PostgreSQL/SFS/object-storage/compute-instance/MysteryBox pollers now abort long-running apply/destroy waits from terminal Nebius SDK operation failures instead of only printing progress summaries.
- Changed explicit `quota-check` output to also print both confirmed checked quota names and coverage-gap reasons as vertical lists under each component, including partial-coverage components such as MK8s when the checked dimensions are sufficient but other dimensions still remain coverage gaps.
- Added guarded built-in destroy recovery for generated Terraform bundles: `destroy` / `terraform destroy` now auto-clear a stale backend lock when the existing local-owner safety checks already pass, retry Terraform destroy once, and if destroy is still blocked by a live MK8s node-group create stuck in terminal-error provisioning, they can delete that stuck node group through the Nebius SDK and retry destroy again inside the same confirmed teardown flow.
- Changed `render <config.yaml>` to always run the same non-strict runtime preflight as `validate` before writing artifacts, so active-source drift, unresolved component dependencies, and Terraform module schema/input mismatches fail before any generated bundle side effects.
- Changed long-running `deploy` / `terraform apply` / `terraform destroy` MK8s monitoring from passive alerting to active fail-fast behavior: node-group API event levels are now read correctly from the live SDK enum fields, terminal node-group failures surface their Nebius error detail directly in status/recovery output without leaking raw SDK object reprs, and apply/destroy abort their Terraform wait loop instead of idling until a generic timeout when the live MK8s API already shows the operation has failed.
- Added live MK8s GPU stack-preset selection to the bundled `mk8s` wizard profile: `inputs.gpu_stack_preset` now comes from the MK8s compatibility matrix, the wizard can auto-select and materialize a singleton compatible preset into `config.yaml`, and new provider option source `mk8s_gpu_stack_presets` is available for other catalog wiring.
- Tightened bundled MK8s GPU-cluster guidance around live preset capability instead of guesswork: the wizard now selects `inputs.gpu_nodes_preset` before `inputs.infiniband_fabric`, the later fabric prompt is shown only when the chosen preset's live SDK metadata allows GPU clustering, stale `infiniband_fabric` values are cleared during interactive edits when the selected GPU shape no longer supports clustering, and runtime validation now fails early on invalid fabric+preset combinations instead of deferring them to Terraform/MK8s admission errors.
- Fixed `component_sources.yaml` wizard-option normalization so explicit `options.args` entries and `skip_prompt_if_no_choices` survive catalog loading; bundled MK8s profile expansions now keep extra provider args such as `preset_path` instead of silently dropping them.
- Standardized explicit CLI severity colors so warnings now render in amber and errors continue to render in red, and aligned the shared shell-scripting skill/template to the same warning/error color contract.
- Refined quota coverage-gap terminal output so repeated internal gap reasons for one component collapse to one concise per-component summary entry in explicit `quota-check`, while routine `create`/`render`/`deploy` output keeps those non-blocking coverage-gap details in the manifest instead of printing them every time.
- Added `quota-check --all-regions`, which replays the current config's quota requirements across all discovered tenant/project regions and prints per-region availability for the same shape while keeping the command's normal pass/fail semantics tied to the config's selected region; plain `quota-check` now suggests that exact rerun command only for confirmed insufficiency, while coverage-gap-only warnings stay informational.
- Changed local `deploy` so built-in MK8s handoff and local kubeconfig refresh still run even when no app charts are enabled, while the no-app path now skips node-readiness and Flux apply/bootstrap checks instead of requiring a Flux phase just to hand off cluster access.
- Added live Nebius quota guard rails for bundled infra components: `create` now warns when the selected project shape already exceeds current tenant/project quota, new read-only `quota-check <config.yaml>` runs the same live assessment on demand, `render` reruns the quota check and stores the report in `generated/nebius-cxcli-manifest.json` while still completing with warnings, and `deploy` now fails fast before Terraform apply when live quota is still insufficient.
- Changed shared-derived component defaults to materialize into `config.yaml` instead of remaining catalog-managed at render time: jump-host `ssh_user_name` and any other `defaults: shared.<path>` targets are now seeded into selected component/chart rows during `create` and `component add`, runtime `render`/`validate` no longer backfill missing shared-derived values from the catalog, and explicit config values no longer conflict with those original catalog seeds.
- Improved bundled MK8s onboarding UX and fail-fast behavior: the wizard profile now guides `inputs.k8s_version`, GPU follow-up fields expand immediately after `gpu_enabled=true` and after GPU platform selection, strict validation treats effective CPU/GPU node-group prerequisites as conditionally required before Terraform apply, and empty Flux renders with no enabled app charts now skip local Flux apply instead of emitting a comment-only `helm-repositories.yaml`.
- Extended jump-host SSH public key handling so private `component_sources.yaml` `shared.admin_ssh.public_key` and per-project `infra.components[].inputs.ssh_public_key` accept inline `ssh-rsa` / `ssh-ed25519` values or readable local `.pub` paths such as `~/.ssh/id_ed25519.pub`; `create`, `component add`, and config-driven commands now resolve those local files, validate supported key formats at runtime, and persist normalized inline key text into `config.yaml` and generated manifests.
- Hardened local Flux deploy/apply waiting so terminal `HelmRelease`/`Kustomization` failures are surfaced from the actual failing workload resource, remaining workload resources are allowed to settle before the command exits, and the default outer wait window now honors rendered workload `spec.timeout` hints plus a short grace period instead of assuming one fixed chart timeout.
- Extended the source-catalog Flux timeout contract so `cli.flux.release_timeout` defines the global default rendered `HelmRelease.spec.timeout`, while per-app `release.timeout` remains optional and only overrides that default when a specific chart needs a different install/upgrade budget; the bundled default is now `5m`, aligned with the upstream Helm/Flux action timeout.
- Fixed the bundled `cert-manager` app catalog defaults to enable chart CRD installation (`values.crds.enabled: true`), preventing fresh-cluster installs from hanging on the startup API check job while cert-manager CRDs are still absent.
- Fixed `render` overwrite prompting so the first render after `create` no longer warns just because the project already has the empty generated scaffold and placeholder `generated/inventory/inventory.md`; the warning now targets meaningful existing rendered artifacts.
- Improved config-path error handling for config-driven commands such as `render`: passing a directory like `generated/` now fails with a targeted “expected project config.yaml file path” message instead of leaking a raw `Is a directory` exception.
- Improved complex wizard prompt wording to ask for a single-line YAML/JSON value, and stopped app components with an empty top-level `values: {}` block from showing a confusing whole-map prompt when no concrete Helm value leaves are known yet.
- Added `wizard.<field>.prompt: false` support so bundled profiles can suppress optional advanced fields from the interactive wizard; the MK8s profile now hides the raw `mk8s_*_overrides` passthrough maps while keeping them available for manual `config.yaml` edits.
- Hardened `create --force` guard rails for existing projects: the CLI now emits a force-specific overwrite warning, requires an extra interactive confirmation before overwriting an existing resolved project folder, and documents that `create --force` does not delete the deployments root or unrelated projects.
- Wired MK8s `inputs.infiniband_fabric` into the built-in wizard profile with a guided, optional fabric selector keyed by the chosen GPU platform and `client_info.nebius.region_id`, using the Nebius GPU-cluster fabric matrix instead of a raw free-text prompt.
- Fixed `create` wizard prompt helper late-binding closures in `cli.py` so Ruff no longer flags `B023` on the deferred module-prompt builders, and tightened the runtime-shape unit coverage to skip post-write validation in the test that only asserts generated config structure.
- Added a central Codex skill at `../../skills/onboard-nbs-cxcli/` for onboarding Nebius Terraform modules into `nebius-cxcli`; it documents the catalog-first onboarding flow, the code-owned layers (`wizard_profiles.py`, `provider_options.py`, `validation_profiles.py`, `runtime_component_validation.py`, `cluster_handoffs.py`, `deployment_status.py`), and the focused test/doc updates expected for each change shape.
- Refined MK8s wizard platform discovery to use live Nebius platform inventory at runtime: CPU/GPU platform prompts now intersect the MK8s compatibility matrix with the selected project's compute-platform list, so the wizard only shows currently available supported platforms while preset choices remain live per selected platform.
- Extended the built-in `ssh-jumphost` and `wireguard-jumphost` wizard profiles to use the live compute platform inventory plus preset chaining, so those VM modules no longer rely on manual `platform` / `preset` entry when project-scoped Nebius choices are available.
- Moved bundled infra runtime validation-profile selection out of the public `component_sources.yaml` catalog and into code-owned defaults in `src/nebius_cxcli/validation_profiles.py`; bundled components now omit repeated internal `validation` markers, and the catalog loader rejects that field instead of carrying a compatibility path.
- Removed the public infra `runtime` block from `component_sources.yaml` and moved the bundled MK8s kubeconfig/bootstrap handoff into code-owned built-ins in `src/nebius_cxcli/cluster_handoffs.py`; auto-discovered Terraform outputs remain the only catalog-facing producer contract, docs/tests were realigned, and inventory/deployment-status helpers now key off `status.kind` instead of old handoff/kind shortcuts.
- Fixed create/component-add wizard handling for declared `component_sources.yaml` `wizard` paths: provider-backed or catalog-declared `inputs.*` / `values.*` fields that are not yet materialized in the payload are now prompted normally instead of emitting a misleading “path not found in config payload” warning, and nested missing containers are created when those prompts are answered.
- Added built-in infra `wizard_profile` support so common Nebius component types can expand to tested wizard wiring from a short profile name, while explicit `wizard` entries remain available as overrides.
- Clarified the docs for `wizard_profile` versus `wizard`: built-in profiles are centralized today in `src/nebius_cxcli/wizard_profiles.py`, and ordinary inputs with no guided choices should omit both fields.
- Removed the generic `vpc` wizard profile and replaced it with component-scoped jump-host profiles so built-in `wizard_profile` names stay aligned with actual TF modules/components rather than a shared service-domain label.
- Tightened the `wizard_profile` contract to a one-to-one component mapping: built-in profile names now match infra component ids exactly, the loader rejects mismatched profile names, and the bundled catalog dropped no-op `shared_file_system` / `mysterybox` profiles instead of carrying empty shorthands.
- Applied the repo Python-project workflow baseline more explicitly: Make now exposes `test-unit`, `test-integration`, `coverage`, and `clean`, `pytest-cov` is available in the dev extras, and the default unit lane blocks live network access unless a test is explicitly marked `integration`.
- Fixed `provider_options.py` type-checker issues in the plugin loader and MK8s version option builder so static analysis no longer reports a callable-signature narrowing error or `OptionChoice` construction from `str | None`.
- Tightened the MK8s control-plane version option builder further to use a direct typed `OptionChoice` append loop, which avoids stale Pyright/Pylance inference complaints around the tuple-construction expression.
- Aligned provider-backed wizard resolution end to end: prompt-time choice loading now normalizes relative provider arg paths the same way strict validation does, `filter_regex` now constrains both displayed choices and manual-entry validation, and fallback warnings preserve resolver/plugin exception text when a provider lookup fails internally.
- Added a dedicated README reference section for `component_sources.yaml` covering the file structure, supported fields, reference syntax, strict-key behavior, and the only regex-capable catalog field (`wizard.<field>.options.filter_regex`).
- Fixed chained wizard/provider prompting for optional infra fields: provider-backed downstream prompts such as MK8s `gpu_nodes_preset` now wait until their `depends_on` selector has a real value, instead of falling back to a misleading manual-entry warning when the upstream platform field was skipped.
- Tightened the README/design docs so the current bundled component catalog is spelled out explicitly: which infra components use matching `wizard_profile` names, which ones rely on plain introspection, and why app components stay on explicit `wizard` only.
- Refreshed `docs/design.md` `Source Code Structure` and test-ownership sections so they now describe the current file layout more concretely, including `wizard_profiles.py`, `cluster_handoffs.py`, source-default/wiring helpers, provider-option ownership, generated-manifest/email-settings helpers, and the focused wizard/provider test modules.
- Clarified in the docs that `component_sources.yaml` `wizard.<field>.options` is the wiring layer between existing Terraform/Helm field paths and Nebius-backed dynamic option lookups, including the chained `depends_on` flow used for platform-to-preset selection.
- Removed the separate `resource_kind` catalog field and made `status.kind` the single canonical Nebius status-watcher contract for infra components; bundled catalog entries, parser validation, tests, and docs now all require the explicit `status.kind` path instead of supporting a shorthand fallback.
- Wired the bundled `mk8s` catalog `inputs.subnet_id` field to the live `project_subnets` provider so `create` now offers Nebius subnet choices for the selected project instead of falling back to a plain manual string prompt.
- Documented explicit developer prerequisites in the README for macOS/Homebrew and Linux/apt, including the core toolchain for `make venv` / `make all` and the optional external CLIs used by specific command paths.
- Reduced `make all` wall-clock time and local/CI timeout risk by reusing the repo `.venv` for the wheel build (`python -m build --wheel --no-isolation`) and running the wheel build in parallel with the lint/test gate after env setup; `make venv` now also upgrades `setuptools` explicitly so the shared environment keeps the required backend version.
- Removed the last name-inference and provider-resource compatibility paths from the source catalog flow: wizard-backed Nebius option lookups now come only from explicit `component_sources.yaml` metadata, infra render emits only source-backed Terraform modules, app source entries no longer accept `runtime`, and docs/tests/help were realigned to that single contract.
- Updated the generated customer GitHub workflow contract to support manual `workflow_dispatch`; manual runs now use `discover --all` for the configured deployments scope so customer repos can rerun plan/apply without relying on a fresh git diff.
- Removed the unused internal `ComponentEntry.origin` field and aligned the test suite with the current source-driven component model so tests no longer carry dead registry/provider-origin scaffolding.
- Refactored `component_sources.yaml` to a keyed `components.infra` / `components.apps` schema with `source.portable` / `source.local`, `wizard`, and infra `runtime.values` / `runtime.contracts`, removed the old `outputs` / `handoff` catalog contract, and aligned create/render/release-catalog/build helpers plus tests and docs to the new source model.
- Fixed component input binding resolution so it now follows the actual enabled source instance instead of assuming the component type id equals the runtime `instance_id`. Unqualified refs such as `mk8s.cluster_id` keep working when exactly one matching source instance is enabled, and catalog bindings can now disambiguate with `<component-id>@<instance-id>.<output-alias>` when multiple instances of the same type are enabled.
- Made Helm source-validation timeouts configurable with `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS` and improved timeout diagnostics so `validate-sources` can be tuned for slow OCI registries instead of failing on a fixed opaque `helm` timeout.
- Fixed the repo Ruff gate so `make lint` and the `nebius-cxcli-ci` workflow now pass again: `cli.py` binds deferred module prompt expansion to the current component loop state, and runtime alias validation uses the simplified single-guard jump-host check expected by Ruff.
- Added regression coverage for the explicit wizard/provider wiring contract: undeclared fields do not trigger Nebius-backed option lookups, while declared `component_sources.yaml` `wizard` fields resolve provider-backed choices only through their configured metadata.
- Clarified the architecture docs to explain why `config.yaml` stays the operator contract while Terraform modules and Helm charts are the provisioning contracts, why the Nebius SDK is used as the dynamic integration layer instead of the primary infra reconciler, and why Terraform output aliases plus `handoff` aliases must be treated as a versioned interface once the CLI/runtime consume them.
- Fixed MK8s wizard field prompting so source-defined literal defaults such as `inputs.cpu_nodes_count: 2` remain editable, GPU-prefixed fields stay hidden until `gpu_enabled=true`, and optional provider-backed fields can now be left blank without falling into an invalid-value re-prompt loop.
- Made MK8s cluster handoff access dynamic instead of hardcoded: the bundled `mk8s` source now resolves `handoff.access` from `inputs.mk8s_cluster_public_endpoint`, so local `deploy` / `flux apply` / `flux bootstrap` / `destroy` / `flux destroy` select the public or private control-plane endpoint automatically. Private-endpoint runs now fail early with explicit network-reachability guidance instead of a generic later `kubectl` dead end.
- Added generated-bundle destroy paths: new top-level `destroy <generated-dir>` now deletes rendered app resources first and then runs Terraform destroy, continuing with infra teardown even when the rendered app delete step fails, and new `terraform destroy` / `flux destroy` commands expose the same destructive workflow in infra-only and apps-only form with explicit confirmation or `--yes`.
- Stopped `destroy` and `flux destroy` from updating `~/.kube/config`; they now use only a temporary kubeconfig for cluster handoff during rendered app teardown, while `deploy`, `flux apply`, and `flux bootstrap` keep the persistent local kubeconfig update behavior.
- Added regression coverage proving `publish-release.sh --prep` remains
  idempotent for unreleased versions: reruns for the same version now stay
  no-op once `Unreleased` is empty and the tag has not been created.
- Changed `publish-release.sh --prep` to fail before editing `CHANGELOG.md` if
  the target tag already exists locally or on `origin`, so duplicate release
  preparation for an already-published version stops immediately.
- Fixed source-checkout runtime version fallback for local release tagging when
  `setuptools-scm` is not installed: `nebius-cxcli.__version__` now derives
  from `git describe` before consulting a generated `_version.py`, so
  `publish-release.sh --publish` no longer rejects a fresh exact tag because of
  a stale local dev-version cache.
- Updated the repo CI and release workflows so they now run
  `validate-sources component_sources.yaml` after `make all`, ensuring the real
  portable component catalog, Terraform modules, and Helm chart sources are
  validated in automation instead of relying only on unit tests.
- Hardened `publish-release.sh` so `--prep` now requires a strictly clean worktree, including untracked files, and first-time pushes from a new local release branch automatically set `origin/<branch>` as upstream instead of failing with Git's "no upstream branch" error; `--publish` now fails before tagging if the target changelog section is missing or empty.
- Made `render` transactional: rerenders now build the replacement bundle under a hidden sibling staging directory and swap it into `generated/` only after the new Terraform/Flux/inventory bundle plus generated manifest are complete, so failed rerenders leave the current bundle intact.
- Clarified docs/help that rerender is now a transactional replace action rather than an eager reset, and documented the Flux-safe workflow: rerender locally, then commit/push one final watched-path snapshot instead of unbootstrapping Flux or publishing intermediate manifest-deletion commits.
- Clarified the `deploy` command contract so help/docs now explicitly say it is the local direct-apply path and does not run `flux bootstrap`; added workflow coverage that generated customer apply jobs use `flux bootstrap` rather than `deploy`.
- Removed the last render-time `generated/flux/flux-system` preservation path. `render` now fully resets `generated/` and deletes any stale legacy Flux bootstrap subtree instead of carrying it forward.
- Reworked email delivery to be disabled by default and operator-local: `nebius-cxcli email --setup` now manages `~/.config/nebius-cxcli/email.yaml`, `bootstrap-ci` syncs non-secret SMTP fields into GitHub Environment variables plus credentials into GitHub Environment secrets, and per-client send/no-send is now controlled by `client_info.notifications.email_enabled` in `config.yaml`.
- Tightened `email <generated-dir>` so it sends only the rendered `inventory.md`, fails fast when that file is missing, and masks tenant/project identifiers in the email subject/body down to their last 4 characters.
- Changed the email contract so generated workflows always run the email step after apply and use `client_info.notifications.email_enabled` as the single send/no-send switch; when email is enabled but SMTP is not configured, the command now warns and continues instead of failing the deploy.
- Changed `bootstrap-ci` to reconcile GitHub SMTP settings from local `email --setup` on every run, including removal of stale `SMTP_*` environment variables/secrets when local SMTP is disabled; `--no-auth-bootstrap` now skips only Nebius CI auth bootstrap.
- Fixed `validate-sources` to accept an optional positional catalog path such as `nebius-cxcli validate-sources component_sources.yaml`, instead of requiring only the global `--component-sources-file` override.
- Split runtime and generated validation into explicit visible phases so long-running `validate` and `validate-generated` calls no longer go silent, and optimized portable validation to reuse resolvable local module metadata when available instead of probing every remote module source during catalog load.
- Clarified root CLI help/docs that `--source-profile` defaults to `portable`; local mode remains the explicit workstation override rather than the implicit test/CI path.
- Clarified `--help` target contracts so the first help screen now tells operators whether each command expects a deployments root directory, `config.yaml`, `generated/`, or an optional `component_sources.yaml` path.
- Clarified `discover` help/docs so they match the implementation: the command accepts the deployments root or any narrower directory under it, including one instance directory or `generated/`, and added CLI coverage for that scoped invocation.
- Fixed scoped `discover` resolution so `--all` and changed-only mode both work from narrower instance directories such as `generated/`, instead of only behaving correctly from the deployments root.
- Clarified top-level and `auth --help` command contracts so `validate-generated` is listed with the generated-bundle commands, `auth` is called out as a no-positional-path command, and `auth --validate-profile` now explicitly documents its all-cached-profiles mode when no project/config target is provided.
- Tightened repo-level Dependabot policy so `.github/dependabot.yml` remains responsible for creating GitHub Actions update PRs, while `.github/workflows/dependabot-auto-merge.yml` is the separate gate for auto-approval and auto-merge of eligible workflow-only GitHub Actions updates, including majors, using the dedicated `dependabot-automerge` environment credential.
- Replaced `azure/setup-kubectl` in generated customer workflows with a direct upstream `kubectl` install step, avoiding the GitHub Actions Node 20 deprecation path.
- Switched render-time Terraform lockfile generation to backend-disabled `terraform init -backend=false` and now remove transient `.terraform/` workdir state afterward, so canonical generated bundles no longer retain local Terraform runtime residue from render.
- Simplified generated customer workflows to rely on the generated-bundle CLI commands for `terraform.auto.tfvars.json` recreation instead of carrying a duplicate inline restore script, and now reconcile the deployments-root `.gitignore` during `bootstrap-ci` as well.
- Removed the unused generated inventory JSON sidecars (`infra.json`, `apps.json`, `mk8s.json`, `postgresql.json`, `sfs.json`); the generated inventory contract is now `inventory.md` only, and refreshes delete any stale legacy inventory JSON files.
- Fixed generated `inventory.md` spacing so section headers and lists remain markdownlint-safe, and clarified in docs that email recipients still come from `client_info.notifications.email` in the generated manifest/runtime config.
- Replaced the split `component_sources.yaml` and `component_sources.release.yaml` model with a single dual-source `component_sources.yaml` schema using required `portable_source` plus optional `local_source` per Terraform module.
- Replaced command-local `--render-profile` with the global `--source-profile {portable|local}` override and added `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE` for workstation-vs-portable source selection across config-based commands.
- Aligned wheel/release packaging and repo workflows with the single-catalog contract, and hardened release-catalog verification so published portable catalogs reject local filesystem `portable_source` entries.
- Removed recently redundant compatibility branches: generated manifests now require `render.module_sources`, the duplicate manifest `render.portable` flag is gone, app release-name aliases are no longer accepted, and seeded infra project defaults now only honor canonical `parent_id` / `project_id` input keys.

## [nebius-cxcli-v0.1.8] - 2026-03-23

- Fixed the `nebius-cxcli` CI and release workflows to run `nebius_cxcli.release_catalog` checks with the repo `.venv/bin/python` created by `make all`, avoiding bare-runner Python import failures under GitHub Actions.
- Hardened `tests/test_setup_build.py` against ambient GitHub Actions build env leakage so setup/build source-selection and release-ref rewrite tests stay deterministic in CI.

## [nebius-cxcli-v0.1.7] - 2026-03-23

- Removed the standalone `nebius` CLI dependency from MK8s kubeconfig handoff and token retrieval; `deploy`, `flux apply`, `flux bootstrap`, and generated customer workflows now use Nebius SDK-backed exec kubeconfig entries through `nebius-cxcli` itself.
- Generated customer workflows no longer install the standalone `nebius` CLI before Flux bootstrap.
- Aligned the main `nebius-cxcli` CI and release workflows to run the same local `make all` verification contract before wheel verification and release publication.
- Aligned CLI help/doc wording for auth profile/config flags and MK8s handoff behavior with the SDK-based contract.
- Tightened `bootstrap-ci` help/docs so the command and flag contract explicitly matches runtime behavior: target `config.yaml` must already be inside the customer git repo, `--github-repo` is only an auth-bootstrap override, and `--github-token-env` only affects GitHub bootstrap/secrets sync.
- Clarified in help/docs that `--cli-ref` selects the `nebius-cxcli` source ref used by the generated customer workflow, not the branch of the customer target repo; kept the option display aligned with Typer's default `TEXT` metavar.
- Fixed runtime version resolution for source/editable checkouts so `nebius-cxcli` now prefers live `setuptools-scm` git state over a generated `_version.py` cache, and `publish-release.sh --publish` now verifies local runtime version/tag alignment before pushing the release tag.
- Clarified MK8s node-readiness behavior before Flux work: `deploy`, `flux apply`, and `flux bootstrap` now probe first and only announce a wait when nodes are actually not `Ready` yet.
- Kept the local Flux phase under one continuous spinner after MK8s handoff so `deploy`/`flux apply` no longer stop and restart the spinner between cluster reachability, Flux API discovery, manifest apply, and rendered-resource readiness checks.
- Added a non-interactive fallback for those Flux phase updates so GitHub Actions and other non-TTY logs get stable printed phase lines instead of relying on transient spinner frames.

## [nebius-cxcli-v0.1.6] - 2026-03-23

- Simplified `bootstrap-ci` so reruns automatically reconcile the CLI-managed customer workflow to the latest generated contract; `--auth-bootstrap` remains enabled by default and workflow-only runs are now the explicit opt-out via `--no-auth-bootstrap`.
- Added regression coverage that `bootstrap-ci --help` and the command surface keep `--auth-bootstrap` enabled by default.
- Fixed customer-side Terraform plan/apply flows for private repos by persisting rendered tfvars in the generated manifest and recreating ignored `generated/infra/terraform.auto.tfvars.json` from that manifest before Terraform runs, both in CLI-generated bundle commands and generated customer workflows.
- Clarified and tested that `deploy <generated-dir>` remains a local/customer-side bundle operation only and does not auto-run `bootstrap-ci` or mutate GitHub CI workflow/environment state.

## [nebius-cxcli-v0.1.5] - 2026-03-22

- Added PR-side coverage for `bootstrap-ci` workflow generation across both development (`main`) and stable tagged (`nebius-cxcli-v<version>`) default CLI refs.
- Hardened `bootstrap-ci` to fail before writing the customer workflow when GitHub auth-bootstrap prerequisites are missing, and documented `--github-repo` as an override over target-repo auto-detection.
- Added explicit render profiles: generator-side `validate` and `render` now default to portable output, while `--render-profile local-dev` keeps checked-out Terraform module paths for workstation testing.
- Hardened generated-bundle validation and customer workflows with `validate-generated --portable`, so PR/apply pipelines reject non-portable local Terraform module sources before plan/apply.
- Simplified wheel/release packaging to bundle the portable catalog via the build override path instead of rewriting the working-tree root catalog during GitHub Actions builds.
- Aligned the generated customer workflow with the example repo by using a shared Python-version env and compact JSON discovery output for deterministic GitHub Actions matrix handoff.
- Added repo-side coverage that the checked-in local and portable catalogs stay semantically aligned except for Terraform module source addresses.
- Added direct tests for the `validate-sources` CLI command surface and GitHub environment-secret bootstrap helpers so those paths no longer rely only on indirect coverage.

## [nebius-cxcli-v0.1.4] - 2026-03-22

- Fixed packaged/bundled `component_sources.yaml` to always use the portable Git-backed catalog so source installs and customer CI no longer fall back to repo-local Terraform module paths.
- Added `bootstrap-ci --cli-ref` so generated customer workflows can be pinned explicitly to a branch, tag, or commit when validating nebius-cxcli changes end to end.
- Stabilized Flux bootstrap fallback coverage so tests no longer depend on live local `kubectl` state when asserting the bootstrap path.

## [nebius-cxcli-v0.1.3] - 2026-03-21

- Hardened release publishing so tagged wheels use the exact tag version and verify bundled portable component sources through shared release-catalog helpers.
- Limited release catalog ref rewriting to this monorepo's module sources and now fail release validation when external module sources are left on floating refs or local paths.
- Added PR-side coverage for release catalog rendering and wheel verification so release packaging errors are caught before tagging.
- Fixed `publish-release.sh --prep` changelog rewriting so moved release notes preserve Markdownlint-safe blank lines around lists and headings.

## [nebius-cxcli-v0.1.2] - 2026-03-20

- Prepare release `v0.1.2`.

## [nebius-cxcli-v0.1.1] - 2026-03-20

- Split the workflow model into generator-side commands for `config.yaml` and customer-side commands for deploying the rendered `generated/` artifacts.
- Added generated bundle manifests, stricter render reset guardrails, and customer-side validation for portable deployment bundles.
- Hardened local deploy and Flux apply/bootstrap flows with better readiness checks, clearer status output, and safer Flux recovery behavior.
- Aligned release packaging and GitHub workflows so published wheels bundle the rewritten portable release catalog instead of local development sources.

## [nebius-cxcli-v0.1.0] - 2026-02-22

- Initial scaffold for `nebius-cxcli`.
- Added `config.yaml` schema validation and deterministic renderers.
- Added Terraform, Flux, discover, inventory, and email commands.
