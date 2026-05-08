# nebius-cxcli Design

## Table of Contents

- [Goal](#goal)
- [Architecture Summary](#architecture-summary)
- [How Flux Works](#how-flux-works)
- [Why Terraform Modules And Helm Charts Are The Contracts](#why-terraform-modules-and-helm-charts-are-the-contracts)
- [Runtime Source Model](#runtime-source-model)
- [Observability](#observability)
  - [Nebius Platform Model](#nebius-platform-model)
  - [cxcli Design Principles](#cxcli-design-principles)
  - [Current cxcli Workflow](#current-cxcli-workflow)
  - [Grafana Dashboards](#grafana-dashboards)
  - [Source And Settings Catalog Contract](#source-and-settings-catalog-contract)
  - [Customer Config Contract](#customer-config-contract)
  - [Runtime Materialization](#runtime-materialization)
  - [Signal Flows](#signal-flows)
  - [Endpoints and Auth](#endpoints-and-auth)
  - [Operational Notes](#operational-notes)
  - [Onboarding Workflow](#onboarding-workflow)
- [Config Model](#config-model)
- [Command Workflow](#command-workflow)
- [Generator-side Commands](#generator-side-commands)
- [Customer-side Commands](#customer-side-commands)
- [Supporting Commands](#supporting-commands)
- [Idempotency Rules](#idempotency-rules)
- [Validation Model](#validation-model)
- [Render Model](#render-model)
- [Auth and CI Bootstrap Model](#auth-and-ci-bootstrap-model)
- [Vendor Scope](#vendor-scope)
- [Runtime Versioning](#runtime-versioning)
- [Source Code Structure](#source-code-structure)

## Goal

`nebius-cxcli` provides a single, repeatable operator workflow:

1. Create one project configuration (`config.yaml`).
2. Adjust source-driven component selection in an existing `config.yaml` over time.
3. Validate generator-side configuration safety/readiness.
4. Render deterministic Terraform and Flux artifacts.
5. Commit the rendered customer artifact bundle.
6. Deploy from the generated bundle and/or bootstrap CI automation.

The design target is source-driven runtime behavior with minimal fixed component logic in core command paths.

## Architecture Summary

Core principles:

- `config.yaml` is the canonical render/reset contract.
- `generated/` is the deploy contract for customer repositories.
- Project-level workflow commands use `config.yaml` as the CLI entrypoint.
- Bundle-level validation can inspect any path under `generated/`; Terraform and
  Flux subcommands stay scoped to `generated/infra/` and `generated/flux/`.
- Source-driven component discovery from `component_sources.yaml`.
- Runtime introspection for module/chart fields and chart dependencies.
- Progressive-enhancement wizard model: infra inputs come from Terraform module variables and app inputs come from Helm values, and optional `wizard` metadata is reserved for explicit Nebius/chart-aware choices or other advanced integration. Complex Terraform types stay native; simple string lists prompt for comma-separated values, other complex inputs accept single-line YAML/JSON values, and product-specific flows such as MysteryBox `inputs.secrets` can provide a guided loop that still writes the same native shape.
- Generic render path for Terraform modules/resources and Flux Helm releases.
- Optional plugin boundaries for provider-specific runtime option lookups and validation.

## How Flux Works

Flux is a shared control plane in the cluster. It does not install one controller
per Helm chart.

Typical shared controllers live in `flux-system`:

- `source-controller`
- `helm-controller`
- `kustomize-controller`
- `notification-controller`

The official Flux install manifest may also install:

- `image-reflector-controller`
- `image-automation-controller`

Those image controllers are shared too. They support automated image update
workflows and are not one-per-chart. The normal `HelmRelease` install path used
by local `deploy` / `flux apply` does not depend on them directly.

The usual object split in this repo is:

- source objects such as `HelmRepository` and `GitRepository` live in `flux-system`
- workload objects such as `HelmRelease` live in the target app namespace

Local direct-apply flow (`deploy` / `flux apply`):

1. Ensure the shared Flux controllers and required CRDs exist.
2. Apply the rendered manifests such as `Namespace`, `HelmRepository`, and `HelmRelease`.
3. `source-controller` resolves the chart source from the rendered source object.
4. `helm-controller` watches the `HelmRelease` and runs the Helm install/upgrade in the target namespace.
5. Flux reports status back on those rendered objects, and the CLI waits on the rendered workload path.

Important distinction:

- a pending `HelmRepository` status does not mean Flux is missing
- a `Ready` `HelmRelease` means the workload release itself is installed and reconciled
- GitOps bootstrap is separate from local apply success

What `flux bootstrap` changes:

- it does not install a different per-chart controller model
- it configures continuous Git-based reconciliation for the cluster
- the key bootstrap objects are `GitRepository/flux-system` and `Kustomization/flux-system`
- after bootstrap, the cluster can keep syncing from the watched Git repo/path

That is why local `deploy` / `flux apply` can succeed before GitOps bootstrap
exists: local apply talks to the cluster directly, while bootstrap turns on
continuous Git-driven sync.

Controller readiness vs workload readiness:

- when local `deploy` has to install Flux controllers itself, it waits for the core controller deployments and required CRDs before applying rendered workloads
- local success is primarily gated by the rendered workload resources such as `HelmRelease`
- the CLI does not require Git bootstrap objects to exist for local apply success
- the CLI also does not require image automation controllers to be part of the basic Helm release success path

Useful checks:

```bash
kubectl get helmreleases.helm.toolkit.fluxcd.io -A
kubectl get gitrepositories.source.toolkit.fluxcd.io -n flux-system
kubectl get kustomizations.kustomize.toolkit.fluxcd.io -n flux-system
```

Interpretation:

- if `helmreleases... -A` is green/`Ready`, the rendered workload releases are healthy
- if `gitrepositories...` and `kustomizations...` are missing in `flux-system`, GitOps bootstrap is not configured yet

## Why Terraform Modules And Helm Charts Are The Contracts

This architecture intentionally uses three different contract layers:

- `config.yaml` is the operator-facing orchestration contract.
- Terraform modules are the infra provisioning contract.
- Helm charts are the app provisioning contract.

The CLI does not use the Nebius SDK as the primary infrastructure reconciler because that would force `nebius-cxcli` itself to become the state engine, diff engine, destroy engine, and portability boundary. Terraform already provides those semantics well:

- desired-state planning and apply/destroy behavior
- state, locking, and drift-aware reconciliation
- reusable module interfaces built from variables and outputs
- portable generated artifacts that can run later in CI or another machine

That matters for this repo because the generator/runtime split is central to the design: the CLI renders a deterministic deployable bundle under `generated/`, then later deploy/apply/destroy commands operate on that bundle rather than reinterpreting `config.yaml` every time.

Terraform modules therefore serve as the canonical infrastructure contract for each reusable infra component. The CLI reads module variables to discover editable inputs, uses Nebius APIs only to enrich the operator experience with dynamic choices and validation, and then renders a plain Terraform root module as the deployable output.

Helm charts serve the same role for apps:

- they preserve the native application deployment contract instead of inventing a CLI-specific app schema
- they keep workload packaging cluster-agnostic
- they let Flux/Helm remain the runtime owner of app reconciliation

The Nebius SDK still has an important role, but it is intentionally narrower:

- validate tenant/project scope
- discover dynamic provider-backed field options
- perform best-effort live quota checks and quota guard rails
- perform readiness/status polling and other runtime checks
- support auth/bootstrap and provider-specific guard rails

That split keeps the CLI generic while still taking advantage of Nebius-specific APIs where they add operator value.

The operator experience follows the same layered design:

- zero-config support for generic modules and charts through runtime introspection
- explicit metadata for Nebius-backed field choices when introspection alone is not enough
- explicit metadata only for advanced integration or ambiguous cases, via optional `wizard`

## Runtime Source Model

Primary source registries (repo root): `component_sources.yaml` and `component_cli_settings.yaml`

- `component_sources.yaml` is the source catalog for reusable Terraform modules and Helm chart components.
- `component_cli_settings.yaml` is the cxcli settings catalog linked by the same `components.<infra|apps>.<component-id>` keys. It owns managed tool versions, observability endpoint templates, Grafana bindings, and cxcli policy for component types.

Sections:

- `component_sources.yaml`
  - `shared.admin_ssh`
  - `components.infra.<component-id>`
    - `source.portable`, optional `source.local`, optional `ui`, optional `status`, optional `defaults`, optional `wizard_profile`, optional `wizard`, optional `input`
  - `components.apps.<component-id>`
    - optional `source.portable`, optional `source.local`, optional `ui`, optional `release`, optional `defaults`, optional `wizard`, optional `input`
  - `source.portable.repo` can be an HTTP/S Helm repo base (must expose `index.yaml`), OCI (`oci://...`), or GitHub tree URL for a git-hosted chart
  - `source.portable.chart` remains the canonical chart basename when it differs from the app id; runtime Helm resolution must use that configured name instead of assuming `id == chart`
  - `source.local.path` is for developer-local Helm chart work and is removed from portable build artifacts
- `component_cli_settings.yaml`
  - `cli.flux.version`
  - `cli.flux.release_timeout`
  - `cli.terraform.version`
  - `observability.endpoints.<read|write>.<endpoint-key>`
  - `components.infra.<component-id>.cli`
  - `components.apps.<component-id>.cli`

`wizard` is intentionally optional. It is not a second required schema that users must maintain when they add a module or chart. The default path is still introspection-first:

- new Terraform modules work from their variables
- new Helm charts work from their chart metadata and `values.yaml`
- optional `wizard_profile` or `wizard` only adds explicit hints when introspection alone is not enough
- when `wizard.<field>.options` is used, the supported keys are `from`, `prefix`, `depends_on`, `args`, `filter_regex`, `auto_select_single`, `auto_select_first`, and `skip_prompt_if_no_choices`; `filter_regex` is the only regex-capable selector, `prefix` and `depends_on` remain plain string/path helpers that are merged into `args` at catalog-load time, `args` carries provider-specific lookup inputs, `auto_select_single` is the opt-in “one live compatible value becomes the default” behavior, `auto_select_first` materializes the first live compatible value after provider-side preference ordering, and `skip_prompt_if_no_choices` lets an optional live-backed field disappear cleanly when the lookup succeeds but yields no valid choices
- when `wizard.<field>.sources` is used, the supported bundled source is `source: static` with `values`; each value may be a plain string or a `{value, label}` mapping so the saved config value can stay concise while the wizard shows a richer operator-facing label
- `wizard.<field>.materialize_default: true` is reserved for fields where accepting the displayed default is a real persisted config choice instead of a virtual convenience default; the bundled MK8s profile uses it for the native MysteryBox ESO sync defaults so selecting MysteryBox with MK8s writes `deploy.targets[].secrets.mysterybox.enabled: true`, `deploy.targets[].secrets.mysterybox.allow_all_namespaces: true`, and `deploy.targets[].secrets.mysterybox.sync_namespaces: [default]`
- interactive component-selection prompts emit one resolved infra/apps summary after dependency resolution finishes; during field input the wizard context stays compact as one Rich-colored line, `Wizard context: Current: <scope> / <component-or-target-feature>`, so long app lists are not repeated before every prompt; fields under `deploy.targets[]` use deploy-target context labels because they are not Terraform module inputs
- dependency-backed wizard fields are gated by the selected upstream component or context: for example GPU validation waits for MK8s GPU, VM collector fields wait for the VM collector, provider-backed choices wait for their declared `depends_on` value, and native MysteryBox ESO sync waits for both MK8s and the Terraform `mysterybox` component
- `status` is the canonical Nebius status-polling contract for infra components; if polling is needed, `status.kind` must be declared explicitly
- Destroy status polling is informational only: when a watched resource is no
  longer visible in the live Nebius API, cxcli reports it as already absent and
  leaves Terraform state/provider reconciliation as the source of truth for the
  actual delete.

`wizard_profile` is the built-in shorthand layer for component-specific Nebius wizard wiring. It expands to a tested `wizard` mapping at catalog-load time. When both `wizard_profile` and explicit `wizard` are set on the same infra component, profile fields load first and explicit `wizard` entries override or extend them. Built-in `wizard_profile` names are one-to-one with infra component ids, and the loader enforces that exact match when a profile is set.

Built-in infra `wizard_profile` definitions are currently centralized in `src/nebius_cxcli/wizard_profiles.py`, not split into one Python file per component. That is an implementation choice, not a schema requirement.

Bundled infra components currently align like this:

- `mk8s`, `managed-postgresql`, `vm`, `wireguard-jumphost`, `ssh-jumphost`, `object-storage`, and `mysterybox` use matching `wizard_profile` names where they have tested guided behavior or prompt suppression.
- `sfs` does not carry `wizard_profile` today because plain Terraform introspection is sufficient for its current UX.
- `mysterybox` uses its profile to prompt the Terraform-native `inputs.secrets` list and hide the runtime-only `inputs.payload_values` helper from prompts; `inputs.secrets` remains the operator-facing backend contract. The wizard requires at least one Secret name, asks for the target Kubernetes Secret name with the MysteryBox name as the default, requires at least one payload key per Secret, collects payload keys/types in a loop, normalizes entered payload keys to uppercase, and treats `q` inside that loop as local backtracking to the previous Secret/key/type question before it exits the whole field. Actual secret payload values stay in runtime `TF_VAR_*_payload_values` input.
- App components do not use `wizard_profile`; they stay on Helm introspection plus optional explicit `wizard` entries.
- The bundled `mk8s` and `vm` settings entries both declare cxcli-owned observability metadata under `components.infra.<id>.cli.observability.*`; the unified architecture, endpoint map, and customer contract are documented in [Observability](#observability).

Bundled MK8s GPU policy is split deliberately between component source data, cxcli settings data, and code-owned semantics:

- `component_sources.yaml` owns chart source selection, release metadata, and unconditional Helm defaults.
- `component_cli_settings.yaml` owns activation rules, role ids, validation images, timeouts, thresholds, and conditional overlays.
- The CLI owns only the rule evaluation. The bundled catalog expresses the current policy as:
  - always require the gpu-operator role
  - require the network-operator role only for MK8s contexts that are both cluster-capable in live Nebius preset metadata and explicitly configured onto the GPU-cluster / InfiniBand path with `inputs.infiniband_fabric`
  - also require the network-operator role for operator-managed B200/B200A stacks that still need RDMA plumbing
  - apply Helm overlays from catalog rules matched on `gpu_stack_source`, GPU-cluster state, platform, and preset
- That split keeps chart metadata and conditional overlays out of Python while still letting the command path choose the correct role set from live MK8s shape decisions.
- Unconditional Helm defaults also carry conservative HA replica settings for platform charts that expose documented safe multi-replica knobs. Grafana's Envoy data plane, Envoy Gateway, cert-manager controller/webhook/cainjector, and External Secrets controller/webhook/cert-controller default to two replicas when the upstream chart default is one; External Secrets also enables leader election. Grafana itself stays on the upstream one-replica default because the bundled chart path uses per-pod SQLite/emptyDir storage; runtime validation rejects `grafana.values.replicas > 1` unless the chart values configure a shared MySQL or Postgres database. DaemonSets, validation jobs, n8n's enterprise-only multi-main path, and charts without a chart-native safe replica knob remain on upstream defaults instead of being forced active-active by cxcli.
- RDMA/GPUDirect detection is intentionally two-stage. The live Nebius project platform/preset inventory is the source of truth for whether the exact selected GPU shape is cluster-capable at all via `allow_gpu_clustering`; cxcli does not hardcode a preset list. The deployment only enters the GPU-cluster / InfiniBand path once `inputs.infiniband_fabric` is actually set, so a cluster-capable shape without that field still stays on the Ethernet-only render/install/validation path.
- The operator app entries keep only Nebius-specific deltas in top-level `defaults`; values that already match the live GPU Operator or Network Operator chart defaults are intentionally left to the charts rather than restated in the catalog.
- On the actual GPU-cluster / InfiniBand path, the bundled catalog now owns the explicit pod-facing RDMA overlay instead of relying on the Network Operator chart default CR. For `gpu_stack_source: nebius_image`, GPU Operator still disables host GPU-driver and NVIDIA Container Toolkit management. If Network Operator is part of the target, GPU Operator disables its own NFD so Network Operator can own that stack end to end; if Network Operator is not part of the target, GPU Operator pins its NFD worker to Nebius GPU nodes. Network Operator NFD and NodeFeatureRules are explicitly enabled because the chart defaults them off. On driverful InfiniBand targets, Network Operator scopes its NFD worker to Nebius driverful nodes, uses the standard Mellanox PCI feature label for the rendered `NicClusterPolicy`, and adds a Helm post-render patch so driverful InfiniBand nodes advertise `rdma/shared_device` without deploying the OFED driver container. The same patch sets `periodicUpdateInterval: 0` for the RDMA shared-device plugin so static KVM passthrough nodes do startup discovery and pod-facing device advertisement without noisy periodic full PCI rescans. For `gpu_stack_source: operator_managed`, the bundled catalog keeps OFED enabled and now adds the same explicit `rdma/shared_device` patch so operator-managed InfiniBand nodes satisfy the same scheduler-visible RDMA contract.
- Deploy-time GPU checks are not modeled as persistent app releases. They are rendered into the generated manifest as validation specs and executed by local `deploy` after Terraform/Flux work finishes.
- The default fast validation is the GPU Visibility test, implemented as a bounded sampled CUDA probe on Ready GPU nodes rather than an unbounded every-node fan-out. The catalog controls `max_nodes`, timeout, and cleanup behavior. The saved report now also includes the selected nodes' device-plugin allocatable snapshot so operators can compare scheduler-visible resources such as `nvidia.com/gpu` or RDMA-style keys with the stronger workload-level CUDA result, but those allocatable keys remain informational rather than the pass/fail gate.
- NCCL is a separate deploy-time validation that is enabled by default for GPU-enabled MK8s clusters and follows the public `NVIDIA/nccl-tests` + Kubeflow training-operator path. The workload manifest is rendered from the first-party `helm-charts/nccl-test` chart, `component_sources.yaml` carries both the developer-local chart path and the portable OCI source pinned to `oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test --version 0.2.8`, and the shared image/tag plus deploy-time benchmark args now come directly from the chart's own `values.yaml`. Local/unit-test default hydration falls back to that checked-in file when `helm` is unavailable so the NCCL validation spec keeps the same first-party defaults. The source chart now keeps conservative 1-GPU smoke-test worker defaults for direct Helm use, while `nebius-cxcli` auto-selects the NCCL transport from the resolved MK8s shape: Ethernet-only shapes render Socket/TCPIP mode, while GPU-cluster / InfiniBand shapes render the RDMA path and enforce the configured bandwidth threshold there. On the RDMA path, the settings catalog appends `NCCL_DMABUF_ENABLE=1` as an MPI environment export so cxcli validation uses NVIDIA's recommended DMA-BUF GPUDirect RDMA path instead of forcing the legacy `nvidia-peermem` path. This is not a GPU Operator or Network Operator Helm value: those charts prepare the driver/RDMA stack, while `NCCL_DMABUF_ENABLE` must be present in the NCCL workload process. cxcli owns that default in `component_cli_settings.yaml` at `components.infra.mk8s.cli.gpu.validations.nccl.rdma_mpi_extra_args`; direct `nccl-test` chart users can set the same runtime export through `benchmark.mpiExtraArgs`. This follows NVIDIA's NCCL environment-variable docs, which describe `NCCL_DMABUF_ENABLE` as enabling GPUDirect RDMA buffer registration through Linux DMA-BUF and default it to enabled when supported, and NVIDIA's GPU Operator GPUDirect RDMA docs, which recommend DMA-BUF over the legacy `nvidia-peermem` kernel module. It still derives the NCCL worker GPU count from the resolved MK8s shape, but worker CPU/memory requests are computed at validation runtime from live scheduler headroom on the selected GPU nodes rather than the nominal preset, and the launcher is pinned to Ready non-GPU nodes when such nodes exist so Ethernet-only 1-GPU clusters remain schedulable without spending GPU-node headroom on the launcher. The only platform-specific MPI rule left on the app entry is the B200 `-mca coll ^hcoll` overlay, which stays catalog-owned because the official Nebius B200 NCCL example includes that flag while the H100/H200 example does not. NVIDIA HPC-X release notes / known issues also say HCOLL is unsupported on GB200/GB300; that is not a direct statement about B200, but it is a nearby Blackwell-era signal against turning the B200 workaround into a shared chart default. See: [NVIDIA NCCL `NCCL_DMABUF_ENABLE`](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html#nccl-dmabuf-enable), [NVIDIA GPU Operator GPUDirect RDMA](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/gpu-operator-rdma.html), [Nebius NCCL guide](https://docs.nebius.com/kubernetes/gpu/nccl-test), [NVIDIA HPC-X General Support](https://docs.nvidia.com/networking/display/hpcvx225/hpc-x-general-support), and [NVIDIA HPC-X Known Issues](https://docs.nvidia.com/networking/display/hpcxv2251/known-issues). The Training Operator remains a transient prerequisite pinned in the catalog's NCCL validation settings rather than a persistent app release, so `deploy` can install/remove it around the `MPIJob` run. Saved NCCL validation reports record `NCCL_DMABUF_ENABLE`, whether it came from rendered MPI args or was left unset, and the derived GPUDirect mode; the combined `deploy-report.md` repeats that summary beside the NCCL bandwidth result. Saved GPU validation reports remain intentionally ordered and compact: practical summary fields stay first, success cases omit bulky raw logs, and failures keep only the relevant log excerpts.
- The bundled NVIDIA path intentionally does not ship a generic built-in "health checker" workload. NVIDIA's own docs separate fast install verification and sample workload validation from ongoing DCGM-based telemetry and deeper DCGM diagnostics. In cxcli, that means deploy-time checks stay focused on operator readiness, bounded CUDA visibility, and optional NCCL, while long-running telemetry/alerting remains the responsibility of DCGM Exporter / Prometheus / Grafana and deeper diagnostics remain explicit administrator workflows rather than something every `deploy` reruns. See: [About the NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/24.9/index.html), [GPU Operator Getting Started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/23.9.0/getting-started.html), [NVIDIA GPU Telemetry](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/index.html), [DCGM Diagnostics](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html), and [NVIDIA Network Operator readiness](https://docs.nvidia.com/networking/display/kubernetes2610/life-cycle-management.html).
- That split still assumes DCGM Exporter itself stays enabled on the GPU Operator release. For GPU-enabled MK8s, DCGM Exporter must stay enabled in GPU Operator. Omitting `nvidia-gpu-operator.values.dcgmExporter.enabled` is valid because the bundled GPU Operator chart defaults it to enabled; explicitly setting it to `false` is rejected. Scraping and pushing those metrics to Nebius Monitoring happens only when the MK8s observability metrics path is enabled. Prometheus scrape wiring remains a chart-level concern under `values.dcgmExporter.serviceMonitor.*`, not a built-in `deploy` validation toggle, and should only be enabled when the target cluster has a Prometheus-operator-compatible observability stack. For the Nebius Observability Agent path, the settings catalog declares the DCGM exporter as an app metric target with `discovery.kind: prometheus_annotations`, so the agent discovers the GPU Operator service through its documented `prometheus.io/scrape=true` service endpoint path instead of through a duplicate `config.metrics.additionalTargets` scrape job. Live testing on the Nebius driverful-image path (`gpu_stack_source: nebius_image`) showed an important nuance: NVIDIA's `dcgmExporter.enabled=true` keeps the source configured in `ClusterPolicy`, but the chart's default NFD worker affinity can leave Nebius driverful GPU nodes without the NFD-owned `nvidia.com/gpu.present=true` label, and those same nodes can carry `nvidia.com/gpu.deploy.operands=false`. The bundled GPU Operator rule therefore pins NFD workers to Nebius GPU nodes only on `nebius_image` targets where Network Operator is not present; GPU-cluster / InfiniBand targets keep Network Operator as the single NFD owner. The DCGM metric target's `managed_gpu_node_policy.{labels,selector,stack_sources}` owns only the Nebius-specific operand labels. Those operand labels are Nebius-specific scheduling policy, not an NVIDIA chart default. When observability and Kubernetes metrics are enabled, cxcli materializes that policy into `inputs.mk8s_gpu_node_group_overrides.labels`, enabling only the DCGM exporter and validator operands while explicitly keeping GPU Operator device-plugin/GFD disabled so the Nebius-managed device-plugin path is not duplicated. During `deploy`, cxcli also reconciles the same settings-owned operand labels onto existing live GPU Node objects matching the catalog-owned selector because MK8s node-group label updates may not back-propagate to already-running nodes.
- Observability architecture, endpoint guidance, project config shape, and onboarding workflow are documented in [Observability](#observability).
- The three built-in validations are intentionally layered to avoid semantic overlap. `operator_readiness` is the cheapest prerequisite gate: policy objects plus scheduler-visible GPUs on Ready nodes. `gpu_visibility` is the bounded single-node data-path smoke test that proves a real CUDA workload can execute. `nccl` is the optional multi-node communication and bandwidth validation: Socket/TCPIP on Ethernet-only shapes, RDMA on GPU-cluster / InfiniBand shapes. The ordering is deliberate so common operator/runtime failures stop before the expensive NCCL phase, while NCCL remains the only bundled check that says anything about distributed GPU communication quality.
- The first gate is intentionally broader than its config-key name suggests. In operator-facing output and the combined deploy report, cxcli labels it `GPU stack readiness` because the runtime check covers GPU Operator and, when required by the selected MK8s shape, Network Operator plus `NicClusterPolicy`.
- `GPU stack readiness` is cluster-wide for Ready GPU nodes rather than sampled: it inspects every Ready node with allocatable GPUs, but it stays cheap because it reads operator policy/state and scheduler-visible resources only. It is therefore a control-plane/data-plane signal, not proof that every node can actually run a CUDA workload.
- Validation cleanup is split deliberately: keep dedicated namespaces for isolation and repeatability, but delete transient validation workloads after each run. For the bounded CUDA smoke test that means deleting the sampled pods while retaining the `gpu-validation` namespace. For NCCL that means deleting the transient `MPIJob` and, if cxcli had to install Kubeflow Training Operator only for that run, deleting that transient prerequisite again while retaining the validation namespace.
- Validation failures that occur before a normal detail report is complete still write a failure JSON artifact with the captured error, so the combined deploy summary reports `FAIL` for that validation instead of treating it as `NOT RUN`.
- On `gpu_stack_source: nebius_image`, Network Operator remains auto-enabled only when the selected MK8s platform/preset is cluster-capable in the live Nebius inventory and the config actually sets `inputs.infiniband_fabric`. That matches Nebius guidance that Network Operator is optional in the other driverful cases. Operators can still enable it manually there, and cxcli keeps `operator.ofedDriver.deploy=false` on the driverful path so optional installs stay chart-managed rather than re-laying host OFED.
- The NCCL threshold uses NCCL's own `average bus bandwidth` metric rather than a raw link-rate threshold. For single-node runs that measures the effective GPU-to-GPU communication path inside the node. For multi-node runs it measures the normalized collective-communication bandwidth across the full topology, including intra-node GPU links and the inter-node network, so it is useful for comparing NCCL health against hardware capability but it is not a direct translation of switch-port line rate.
- Bundled MK8s boot-disk defaults now split cleanly between settings-owned policy and code-owned evaluation. `component_cli_settings.yaml` owns `components.infra.mk8s.cli.boot_disk_defaults.<cpu|gpu>.default_type` plus ordered `rules` keyed by resolved preset resources such as vCPU, RAM, and GPU count, while the CLI materializes explicit `inputs.<cpu|gpu>_nodes_boot_disk_size_gib` / `inputs.<cpu|gpu>_nodes_boot_disk_type` values from the effective node-group platform/preset during `create`, `component add`, and runtime config loading. Live provider preset metadata is preferred when available and preset-name parsing is the fallback. The first matching rule becomes the cxcli-owned explicit default for that shape, and only shapes that do not match any rule fall back to the heuristic. High-performance SSD types still round to their required 93 GiB multiples; regular `NETWORK_SSD` sizes remain exact GiB values so `93 GiB` and `1023 GiB` catalog defaults stay stable instead of being inflated to synthetic 32 GiB buckets. Explicit first-class inputs or `template.boot_disk` overrides remain authoritative.

When `wizard.<field>.options` is present, it acts as wiring between an existing Terraform input or Helm value path and a guided option provider. The field itself still belongs to the module/chart contract; the catalog metadata only tells the CLI how to fetch valid choices for that field. Declared wizard-only helper fields can also carry `default`, which behaves like a virtual prompt default: the operator sees and can change the value in wizard mode, but unchanged defaults are not written back into `config.yaml`. For Nebius-backed flows, that means the operator-facing destination remains something like `inputs.cpu_nodes_platform`, while `from: mk8s_compatible_platforms`, `from: compute_platform_presets`, `from: mk8s_gpu_stack_presets`, `from: mk8s_node_group_os_values`, `from: mk8s_boot_disk_types`, or `from: mk8s_control_plane_versions` tells the CLI which Nebius API-backed or Nebius-contract-backed lookup to execute. For MK8s platform fields, the provider now treats the MK8s compatibility matrix as the authoritative support filter and, when a project id is available, intersects that set with the selected project's live compute-platform inventory so the wizard only offers currently available CPU/GPU platforms. The bundled MK8s flow materializes `inputs.cpu_nodes_os`, `inputs.gpu_stack_preset`, and `inputs.gpu_nodes_os` from that same compatibility matrix using catalog-owned preference ordering, while `inputs.gpu_stack_source` is a guided fixed choice between `nebius_image` and `operator_managed` that controls whether the module renders Nebius-managed `gpu_settings.drivers_preset` or uses the operator-managed GPU Operator stack. Its wizard labels make driver ownership explicit: `nebius_image` means the Nebius GPU node image already includes the host NVIDIA driver/toolkit, and `operator_managed` means GPU Operator installs and manages those host components. The bundled MK8s `inputs.infiniband_fabric` field is provider-wired too, but the important decision is no longer a static platform heuristic: after the operator chooses `inputs.gpu_nodes_preset`, the CLI checks the exact selected platform/preset in the live Nebius project inventory, uses the preset's `allow_gpu_clustering` metadata as the source of truth for RDMA capability, only offers the optional fabric prompt when that capability is present, and clears stale interactive fabric values if a later preset change removes that support. That keeps the concepts separate on purpose: live Nebius metadata decides whether the shape is cluster-capable, while setting `inputs.infiniband_fabric` is the operator-facing step that actually enables the GPU-cluster / InfiniBand path for render-time operator selection and deploy-time GPUDirect/NCCL behavior. The preset labels now make the interconnect contract explicit too: single-GPU non-clusterable shapes are marked as Ethernet-only testing/dev shapes, while clusterable multi-GPU shapes are marked as the InfiniBand path for distributed training. When tenant/project/region context is available, the same wizard step also queries the live Nebius Capacity Dashboard `resource-advice` surface for the exact GPU platform+preset and uses those live rows as the source of truth for the offered fabric names, current on-demand/reserved availability annotations, and the recommended default while still preserving the optional field's skip/unset behavior; preset summaries aggregate matching fabric rows per selected platform/region/preset so an H100 reserved lane is not hidden by a stronger H100 on-demand fabric, and H100/H200 rows remain separated even when the preset names match. Because reservations are fabric-bound, the fabric prompt recommends the best reserved-capacity fabric first when any matching reservation slots exist; otherwise it recommends the best regular/on-demand fabric. GPU preset prompts can use that same live advice to rank/annotate shape choices before the operator picks a fabric. The Capacity Dashboard can still report fabric-scoped capacity rows for single-GPU shapes because capacity is physically partitioned that way; cxcli uses those rows only to rank shape availability and does not expose a fabric selector unless the live preset metadata says GPU clustering is supported. When a cluster-capable shape has no live fabric rows, the wizard falls back to manual entry for that optional field instead of relying on a baked-in static fabric list. Runtime validation also treats live Capacity Dashboard fabric rows as the source of truth for concrete `infiniband_fabric` values when those rows are available, while the selected preset's `allow_gpu_clustering` metadata remains the source of truth for whether the shape is RDMA-capable at all. Wizard metadata can also suppress optional advanced fields from interactive prompting with `prompt: false`; the bundled MK8s profile uses that for the compatibility-matrix-derived image inputs and the raw `mk8s_*_overrides` passthrough maps. The first-class boot-disk fields are now part of the interactive flow: once the effective node-group shape is known, cxcli pre-fills boot-disk size from the first matching ordered size rule for that preset-resource shape, falls back to the heuristic only when no explicit rule matches, prompts with guided Nebius disk-type labels, and refreshes the derived size when the selected shape/type changes unless the operator has already set a custom first-class or `template.boot_disk` value. The guided boot-disk prompt intentionally offers the recommended SSD-backed types `NETWORK_SSD`, `NETWORK_SSD_NON_REPLICATED`, and `NETWORK_SSD_IO_M3`; other module-supported values such as `NETWORK_HDD` remain manual-config-only. The MK8s preemptible switches stay ordinary first-class module inputs: `inputs.cpu_nodes_preemptible` and `inputs.gpu_nodes_preemptible` render the matching node-group `template.preemptible = {}` block for the selected CPU or GPU node group. The VM wizard keeps the Compute preemptible contract in one place too: it shows preemptible follow-up fields only for GPU platforms, suppresses direct recovery-policy prompting, and materializes `inputs.recovery_policy: FAIL` when `inputs.preemptible_enabled=true` so the VM module can render `preemptible.on_preemption = "STOP"` with a valid recovery policy. Deploy-time MK8s GPU checks now use a target-facing contract under `deploy.targets[].validations.mk8s_gpu.*`, not fake Terraform module inputs or one project-global validation block. The settings catalog still owns the defaults in `component_cli_settings.yaml` `components.infra.mk8s.cli.gpu.validations`, and the MK8s wizard still exposes those same toggles, but the chosen per-target values persist in `config.yaml` under the matching `deploy.targets[]` row so they clearly belong to the CLI deploy surface. The legacy fake-input path `infra.components[].inputs.gpu_validation_overrides` is intentionally unsupported and fails fast; operators must use the canonical `deploy.targets[].validations.mk8s_gpu.*` contract instead. When GPU nodes are enabled, operators can toggle operator-readiness, GPU-visibility, and NCCL checks and tune the visibility/NCCL node fan-out per target; the NCCL bus-bandwidth threshold remains part of the same target contract, but the wizard hides that threshold field until the current MK8s shape is actually on the GPU-cluster / fabric path where RDMA thresholding applies. `deploy.targets[].validations.mk8s_gpu.health_checker.enabled` is a reserved app-policy hook, not a built-in validation kind: it can auto-enable a catalog app with role `health_checker`, but cxcli does not ship a built-in health-check runner and omits that setting from bundled target defaults unless an active catalog actually supplies such an app. Local `deploy` can temporarily bypass the real built-in validation kinds with `--skip-validations` or repeatable `--skip-validation <kind>` flags, which are one-run overrides and do not rewrite `config.yaml`. If the resolved MK8s GPU inputs imply required operator apps, the wizard now auto-enables and seeds those app rows after the infra pass and before the app pass, so the same `create` or `component add` run can still show their app prompts instead of only materializing them later in the saved config. Component-level phase prompts preserve that sequencing: answering `n` to `Configure '<component>' component fields now?` skips only that component and continues with the remaining selected components, while `q` still stops the wizard. The interactive field wizard also prints explicit `Infra` and `Apps` section banners and echoes each answered field as a terminal-visible `Selected <path> = <value>` line with secret-like paths redacted, so operators can scan the terminal history before reading the saved `config.yaml`. Operator readiness itself is now grounded in live cluster state rather than NVIDIA label folklore: the control-plane gate is the pair of operator policy objects (`ClusterPolicy` and, when required, `NicClusterPolicy`), GPU data-plane readiness still requires Ready Kubernetes nodes to advertise allocatable `nvidia.com/gpu`, and the actual GPU-cluster / InfiniBand path additionally requires those same Ready GPU nodes to expose scheduler-visible RDMA-style allocatable resources such as `rdma/shared_device`. The saved report now also captures `NicClusterPolicy.status.appliedStates` plus daemonset rollout summaries so a green control plane is not mistaken for pod-facing GPUDirect readiness. If a GPU Operator condition reason is stale or conservative, for example `NoGPUNodes`, allocatable GPUs on Ready nodes remain the data-plane signal cxcli uses. Public MK8s node-group `boot_disk` currently exposes size/type only, so optional SSD NRD / SSD IO M3 encryption remains out of scope for cxcli until Nebius exposes that field on the MK8s surface. For current disk characteristics and pricing, see [Types of storage volumes in Compute](https://docs.nebius.com/compute/storage/types) and [Compute pricing in Nebius AI Cloud](https://docs.nebius.com/compute/resources/pricing). `depends_on` is the chaining input for multi-step lookups, such as querying presets for the platform selected in a previous prompt, and that relative path is normalized against the active component instance for both prompt-time choice loading and strict provider-value validation. Chained provider-backed fields are only prompted after their dependency field has a concrete value, and enabling a sibling `<prefix>_enabled` toggle now expands those dependent prompts immediately into the remaining wizard flow instead of deferring them to a later pass. `filter_regex` is the only regex-capable selector, and it is applied consistently to displayed choices and manual-entry validation. Fields that do not need guided choices should rely on normal Terraform/Helm introspection and omit both `wizard_profile` and `wizard`.

Built-in infra `wizard_profile` names currently include:

- `managed-postgresql`
- `mk8s`
- `mysterybox`
- `object-storage`
- `ssh-jumphost`
- `vm`
- `wireguard-jumphost`

Component output and handoff contract:

- Terraform outputs exposed by a source module are exported automatically under their normalized names.
- Consumer-side `input` bindings use those exported Terraform output names.
- Cluster handoff for kubeconfig/bootstrap is code-owned, not catalog-declared.
- Today the bundled `mk8s` component is the only built-in cluster handoff source. It uses Terraform output `cluster_id` and derives endpoint access from `inputs.mk8s_cluster_public_endpoint`.
- Multiple enabled instances of that handoff source can be rendered and applied as infra, with each Terraform output namespaced by `instance_id`. For MK8s, new wizard-created rows use `inputs.cluster_name` as the `instance_id` when the row still has the generated placeholder id, so cluster targets stay human-readable (`cluster1`, `cluster2`). When built-in cluster targets exist, enabled app rows bind to one target by using the target cluster id as `apps.charts[].instance_id`; target-scoped deploy settings bind through `deploy.targets[].instance_id` using the same cluster id. The full app identity remains `<chart-id>@<instance-id>` such as `nvidia-gpu-operator@cluster2`. Render derives target-scoped deploy metadata into the generated manifest with `deploy.targets[].target_ref` equal to `deploy.targets[].instance_id` and writes one flat Flux subtree per target under `generated/flux/targets/<target-id>/`. Generated-bundle commands reject missing or divergent `target_ref` values instead of falling back to old component/chart identities. Commands that need Kubernetes access, such as `deploy`, `flux apply`, `flux destroy`, `flux bootstrap`, or deploy-time validations, select one target with `--target <instance-id>` or run every target with `--all-targets`. Infra-only `deploy` skips the optional kubeconfig refresh when multiple handoff-capable clusters are enabled and no selected work needs Kubernetes access.

Source profile contract:

- `portable` is the default and always resolves Terraform modules from `source.portable`.
- `local` prefers `source.local` and falls back to `source.portable` when `source.local` is blank.
- The active profile is chosen globally by `--source-profile`, then `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`, then default `portable`.
- The root CLI help should state that default explicitly so workstation users do not assume `local`.
- Metadata discovery is allowed to prefer a resolvable `source.local` even when the active profile is `portable`, so local/CI validation can inspect module outputs and variables without paying remote Git probe cost for every catalog entry.

Source validation requirements (`validate-sources`):

- Terraform components (`components.infra.<id>`):
  - `<component-id>` token must match runtime component id format (lowercase letters/digits/hyphens).
  - `source.portable` is required.
  - `source.local` is optional.
  - `validate-sources` validates whichever module source is active for the resolved source profile.
  - Active local filesystem sources may be relative or absolute.
  - Relative local paths are resolved from the active `component_sources.yaml` file location first.
  - Active local sources must resolve to an existing directory with at least one `*.tf` file.
  - Every module source is install-checked with `terraform init -backend=false`, so broken remote refs and missing git/auth access fail before deploy-time commands start.
  - Fast module-contract validation also runs against the resolved module directory:
    - missing `versions.tf`, missing `required_version`, and missing `required_providers` are hard failures
    - provider/backend blocks inside child modules are hard failures
    - missing canonical files such as `main.tf`, `variables.tf`, `outputs.tf`, `README.md`, or runnable `examples/` roots are warnings
  - Supported Terraform module source formats are only:
    - relative local path
    - absolute local path
    - Git repo source address such as `git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3`
  - Plain `http://` or `https://` module URLs are rejected. Users must provide the Terraform Git source form instead.
  - Registry-style and `oci://` Terraform module sources are rejected.
  - All Terraform outputs exposed by the module are exported automatically under their normalized names.
  - If a custom module is used behind the bundled `mk8s` component, it must still expose Terraform output `cluster_id` for local `deploy` and CI kubeconfig bootstrap flows.
- Helm chart sources (`components.apps.<id>`):
  - HTTP repo mode: `source.portable.repo` is a Helm repository base URL; `index.yaml` must be readable; chart name and configured version must be present in index entries.
  - OCI mode: `source.portable.repo` is an OCI repo prefix (`oci://...`); `source.portable.chart` provides the chart name, and runtime validation/rendering/dependency lookup keep using that chart basename even when the app id differs.
  - GitHub tree mode is supported for git-hosted charts via `source.portable.repo`: `https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`.
  - Local chart mode is supported via `source.local.path` when the active source profile is `local`.
  - Helm chart sources are fail-fast validated with `helm show chart`; missing Helm, missing Git for Git tree chart sources, bad refs, unreachable repos, and chart/version mismatches are hard failures. `create` performs missing source-validation tool preflights before identity prompts when `--validate-sources` is enabled.
  - `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS` can raise the validation timeout for slow OCI registries or chart sources without changing the catalog.
  - Fast chart-contract validation also materializes the resolved chart and checks for `Chart.yaml`, `values.yaml`, `templates/`, and essential `Chart.yaml` metadata (`apiVersion`, `name`, `version`).
  - Missing `README.md` is a warning only for local chart paths; remote Helm chart packages may omit it without warning because that is upstream packaging policy rather than a customer action item.

Accepted Terraform module source examples:

- relative local path: `../../platform-infra/modules/mk8s`
- home-relative local path: `~/repos/platform-infra/modules/mk8s`
- Git repo source: `git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3`

Local Terraform module sources are rendered as resolved local filesystem paths. If you need a pinned remote ref, declare an explicit `git::...?...ref=...` source instead of combining a local path with `version`.

Accepted built-in MK8s handoff example:

```yaml
cli:
  flux:
    version: v2.8.0
  terraform:
    version: 1.14.1

components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main
        local: ../../platform-infra/modules/mk8s
      defaults:
        inputs.cpu_nodes_count: 2
        inputs.mk8s_cluster_public_endpoint: true
```

This is the catalog shape for the bundled MK8s component after the handoff contract was moved into code.
Terraform outputs are still exported automatically, and the built-in MK8s handoff consumes Terraform output `cluster_id`.
Endpoint access still resolves from `inputs.mk8s_cluster_public_endpoint`, but that binding now lives in code instead of `component_sources.yaml`.
Helm chart definitions stay cluster-agnostic; cluster selection is an operator-side binding in `config.yaml` through `apps.charts[].instance_id`, while `deploy`/`flux bootstrap`/CI resolve each built-in MK8s target separately and then run Flux/kubectl against that target's rendered Flux subtree. App chart identity is the pair `<chart-id>@<instance-id>`; target-bound app rows use the target id as `instance_id`, so `instance_id: cluster2` is the single authored cluster binding without hiding the chart type. Internal generated rows may also carry `target_ref`, but that field is a derived runtime alias for the same target `instance_id`, not a second user-facing binding.
Flux controller installation version for local `deploy` is configured in `component_cli_settings.yaml` under `cli.flux.version`.
Default rendered Helm release timeout is configured in `component_cli_settings.yaml` under `cli.flux.release_timeout`.
Managed Terraform CLI download version is configured in `component_cli_settings.yaml` under `cli.terraform.version`.
For app entries, unconditional chart defaults stay at top-level `defaults` in `component_sources.yaml`, while context-sensitive MK8s GPU policy lives in `component_cli_settings.yaml` under `components.apps.<id>.cli.mk8s_gpu_policy.rules`. Each rule can auto-enable the app and/or inject conditional chart defaults when the selected GPU context matches, so the settings catalog keeps one rule list instead of splitting activation and value-default behavior across separate fields. When multiple rules need the same chart-value overlay or post-render patch body, the settings catalog can define that once under `cli.mk8s_gpu_policy.default_sets` or `post_render_patch_sets` and let individual rules reference it with `defaults_from` or `post_render_patches_from`; that keeps the important selectors and CR patch content catalog-owned without duplicating them inline. Post-render patch text can use `{chart_version}` when an operand image tag must track the app chart's `source.portable.version`, such as the Network Operator RDMA shared-device plugin tag. The same `cli` namespace also carries optional app-side observability metadata under `cli.observability.metric_targets` for app-specific metrics endpoints and GPU node-label prerequisites. For the bundled `nvidia-gpu-operator`, both MK8s GPU stack modes now force `values.driver.nvidiaDriverCRD.enabled=false`, because the bundled GPU Operator chart path for Nebius `NVIDIADriver` CRs can fail during Flux install. The Nebius-image rule also disables `values.driver.enabled` and `values.toolkit.enabled` because Nebius-managed GPU images already ship the host GPU driver plus the NVIDIA Container Toolkit runtime, while the `operator_managed` rule keeps those two host-side paths enabled so GPU Operator installs and manages the host stack. cxcli intentionally does not pre-seed `nvidia.com/gpu.deploy.operands=true` or `nvidia.com/gpu.deploy.device-plugin=true` on operator-managed targets; those labels are manual forced-operand controls for preinstalled-driver workflows, not the source of truth for the operator-managed lifecycle. Separate rules suppress GPU Operator's NFD whenever the bundled Network Operator path is selected, explicitly enable Network Operator NFD/NodeFeatureRules for those targets, and add GPU Operator's Nebius GPU-node NFD affinity only when a `nebius_image` target is not on the GPU-cluster path. In multi-target MK8s projects, required GPU app rows are normalized per target and GPU policy defaults plus post-render patches are resolved through each row's target-scoped `instance_id`, so a GPU-cluster / InfiniBand target and an Ethernet-only 1-GPU target can coexist without sharing incompatible operator values. Native MysteryBox-to-Kubernetes sync is also target-scoped: `deploy.targets[].secrets.mysterybox.enabled=true` auto-enables `external-secrets` for that target and renders ESO-native resources into a generated post-Flux manifest that local deploy/Flux apply submits after the external-secrets HelmRelease is Ready. Selecting the Terraform `mysterybox` backend with any MK8s target also auto-enables the same target-scoped `external-secrets` row during `create` and `component add`, before the component field wizard starts, so the dependency is visible with the other app selections. The source-catalog `release.install_after` field is app-only: it auto-selects prerequisite app components and feeds Flux `dependsOn` ordering between Helm releases. MK8s GPU policy-managed chart-value paths are authoritative during `create`, `component add`, direct `config.yaml` normalization, and `render`: cxcli rewrites the currently applicable policy paths from the settings catalog and clears no-longer-applicable policy paths instead of preserving stale older operator values from `config.yaml`.

The bundled Soperator app also uses source-owned CLI metadata:
`components.apps.soperator.cli.soperator_nodesets_profile`. The selected
`apps.charts[].profile` chooses a named profile, defaulting to `nebius-gpu-v1`.
Built-in profile choices are `nebius-cpu-v1`, `nebius-gpu-v1`, and
`nebius-mixed-v1`. Each profile seeds Terraform-owned MK8s generic
`node_groups`, sibling SFS filesystems, and matching Soperator chart values.
The mixed profile deliberately creates separate homogeneous worker NodeSets,
`worker-cpu` and `worker-gpu`, and maps Slurm partitions to those NodeSets; it
does not create a mixed-hardware NodeSet. The Soperator chart derives Slurm
`Gres=gpu:<count>` from each GPU NodeSet's `slurmd.resources.gpu` value during
render, so profile data does not duplicate the GPU count while Slurm GPU
partitions still support `--gres=gpu:*` requests. NFS is intentionally outside
the MK8s node-group profile and remains an optional VM-based infra component
whose Terraform outputs are bound into chart `externalNfs` values by render.
The Soperator app wizard also exposes `values.partitionProfile`. The default
`shape-default` keeps the shape partitions from the selected nodesets profile,
while `with-debug-long` overlays extra `debug` and `long` policy partitions in
the rendered `SlurmCluster`. Slurm features stay on the rendered `NodeSet`
`nodeConfig.features` list, so hardware labels such as `h100`, `a100`,
`highmem`, and `infiniband` are attached to homogeneous worker NodeSets rather
than modeled as partition fields.

Module outputs consumed by app bindings or built-in handoff behavior must be treated as a versioned interface.
In practice that means names such as `cluster_id` are not just internal module details once the CLI, generated manifest, app bindings, or deploy/bootstrap flows consume them.
Renaming, removing, or changing the meaning/type of one of those outputs is a breaking contract change for the component, even if the underlying Terraform module still applies successfully.
When a module evolves, either keep those exported outputs stable or introduce the change as an explicit contract/version change rather than silently reusing the old component identity with different output semantics.

Flux namespace architecture:

- `flux-system` is the shared Flux control namespace in this project.
- Flux controllers run in `flux-system`.
- Flux source objects such as `HelmRepository`, `GitRepository`, `OCIRepository`, and `Bucket` typically live in `flux-system` as shared inputs for one or more workloads.
- Namespaced consumer objects such as `HelmRelease` live in the target workload namespace and can reference a source object in `flux-system`.
- The resulting workload pods and services are created in the workload namespace, not in `flux-system`.
- A workload namespace does not require its own dedicated source object unless it truly consumes a different chart or repository source.

Generic component wiring model:

```yaml
components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main
        local: ../../platform-infra/modules/mk8s

  apps:
    demo-app:
      source:
        portable:
          repo: https://example.invalid/charts
          chart: demo-app
          version: 1.0.0
      release:
        namespace: demo
        name: demo-app
        timeout: 10m
      input:
        values.global.clusterId: mk8s.cluster_id
```

Contract rules:

- Source-defined values live under `defaults`.
- Terraform module defaults must target `inputs.*`.
- Helm chart defaults must target `values.*`.
- Shared-derived defaults use `shared.<path>`.
- Producers expose Terraform outputs from their source modules.
- Consumers declare target paths under `input`.
- `input` is reserved for component-output references; literal values and shared-derived values must use `defaults`.
- References use `<component-id>.<output-alias>` or `<component-id>@<instance-id>.<output-alias>`.
- Unqualified references resolve only when exactly one enabled source instance matches that component type.
- Both producer and consumer must be declared in `component_sources.yaml`.
- Component ids must be globally unique across `infra` and `apps`.
- Literal `defaults` seed starter config during `create` and apply as runtime fallback when the target field is missing.
- Shared-derived `defaults` resolve from top-level catalog `shared` values, and `create`/`component add` materialize those effective values into the selected component rows in `config.yaml`.
- The one intentional exception is `shared.admin_ssh.public_key`: when a private active catalog sets it and a selected infra module declares `ssh_public_key`, `create`/`component add` accept either inline `ssh-rsa` / `ssh-ed25519` content or a readable local `.pub` path, resolve it locally if needed, and copy the normalized inline key into the per-project `config.yaml`.
- Terraform-backed outputs render as native Terraform module references for infra consumers.
- Terraform-backed outputs for app consumers resolve from Terraform state during `deploy` and `flux bootstrap`.
- Plain `render` can resolve Terraform-backed app bindings only when prior Terraform state already exists; otherwise it fails fast.

Resolution precedence:

1. CLI `--component-sources-file`
2. current working directory `./component_sources.yaml`
3. env `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`
4. user file `~/.config/nebius-cxcli/component_sources.yaml`
5. global file `/etc/nebius-cxcli/component_sources.yaml`
6. repo default `component_sources.yaml` (when present)
7. bundled package default (`nebius_cxcli/component_sources.yaml` packaged inside the install)

Catalog-selection policy:

- Automatic catalog resolution is a convenience default for interactive use.
- `validate` and `render` default to the `portable` source profile, so the normal generator path produces deployable artifacts rather than workstation-local Terraform module paths.
- Installed-package fallback is portable by default: when no external catalog override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources.
- `--source-profile local` is the explicit escape hatch for workstation testing against checked-out Terraform modules.
- `--component-sources-file` and `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` remain catalog-selection overrides, not the primary portable-vs-local switch.
- `component_sources.yaml` stays semantically identical across local and portable use because both source forms live in one file; profile choice only changes transport, not feature contract.
- Generated-bundle commands should not require the original render environment's local source catalog in order to resolve Terraform module paths.

Instance self-containment:

- `--component-sources-file` is a global optional override for the active source catalog path.
- When omitted, nebius-cxcli resolves the default file name `component_sources.yaml` from the standard search order above.
- The active source catalog and its sibling `component_cli_settings.yaml` are loaded as a catalog pair. cxcli does not raw-merge the YAML trees; it performs a typed join by `(scope, component-id)`, so `component_cli_settings.yaml` `components.infra.<id>.cli` attaches to the matching Terraform component and `components.apps.<id>.cli` attaches to the matching Helm chart. Settings for unknown component ids fail validation.
- `component_sources.yaml` is source input for `create` and for config-based runtime validation.
- `create` component selection uses the full resolved `component_sources.yaml` catalog.
- `component list`/`component add`/`component remove` also use the full resolved `component_sources.yaml` catalog against an existing `config.yaml`.
- In `component_sources.yaml`, `ui.enabled` controls default selection state only.
- `create` persists only selected `infra.components[]` and `apps.charts[]` rows in `config.yaml`.
- When `create` overwrites an existing resolved project folder, it recreates that one folder from scratch, restarts client-info prompts from the normal create defaults, and rebuilds infra/apps selections plus component rows from the current create inputs.
- `component add` preserves existing rows and values, appends new selected rows, and prompts only for newly added component fields.
- `component add` is idempotent for already-enabled exact selectors. Repeat selectors are skipped, and adding another infra or app-only row requires an explicit new `instance_id` with `<component-id>@<new-instance-id>`.
- In non-interactive mode, `component add` requires target-bound app charts to name the target explicitly when multiple cluster targets exist, for example `n8n@cluster2`; the saved app row uses `instance_id: cluster2`. A target-bound chart can be enabled once per chart id and cluster target; duplicate `<chart-id>@<target-id>` adds are skipped without writing a second row.
- When `component add` introduces the first built-in cluster target into an app-only config, cxcli rewrites each unambiguous existing app row to the target-bound identity by setting `instance_id` to that cluster target. Duplicate unbound rows for the same chart remain an explicit operator fix instead of being guessed.
- Interactive `component add` prompts for infra first and can complete an infra-only add without any app selection. It prompts for apps only when no infra was selected or when the operator explicitly chooses to add apps too.
- `component add` validates `component_sources.yaml` by default, matching `create`; `--no-validate-sources` is the explicit escape hatch.
- `component add` also revalidates the existing Nebius tenant/project scope before provider-backed field prompts so dynamic option failures surface clearly.
- `component remove` deletes selected rows and, when a cluster target is removed, cascades removal to app chart rows plus `deploy.targets[]` settings bound to that target. It still fails when the resulting config would break component bindings or chart dependencies.
- `config.yaml` does not embed `component_sources`.
- Config-based commands resolve sources from the active `component_sources.yaml` resolution path.
- Canonical project path shape is `<deployments-root>/<tenant-folder>/<project-folder>/config.yaml`.
- `create` still takes `tenant_id` / `project_id` as the project identity inputs. Folder names are resolved from the validated Nebius names only after ID validation succeeds, and runtime identity continues to come from `config.yaml`.
- App chart defaults (`release.namespace`, `release.name`) can be edited in wizard mode or overridden in non-interactive mode with `--app-namespace` and `--app-releasename`.
- App chart `release.timeout` is optional catalog metadata for Flux `HelmRelease.spec.timeout`; when omitted, the chart inherits `cli.flux.release_timeout`. That keeps a global default in the catalog while still allowing per-chart overrides without hardcoding chart-specific logic in the deploy loop.
- `deploy.targets[].observability.*` is the canonical customer-facing MK8s observability contract, and `deploy.observability.vm.*` remains the VM observability contract. cxcli renders those deploy settings into infra labels and app chart values during render/deploy, keeping `config.yaml` organized under `infra`, `apps`, and `deploy`.

Wizard field/option model:

- Infra input fields are discovered from module `variables.tf` (required and optional variables for source-backed modules).
- Required variables are prioritized during prompts and enforced by strict validation.
- When an MK8s wizard answer under `deploy.targets[].observability.*` makes the bundled `nebius-observability-agent` required, cxcli auto-enables that target-bound app immediately and announces the adjustment before later infra prompts. The later app-phase prompt only controls whether to customize chart values; skipping it keeps the selected app defaults.
- Runtime-required infra inputs can be promoted above raw Terraform metadata when the CLI needs a stronger contract for a specific component.
- Prompt labels include Terraform type hints (for example `string`, `number`, `bool`) and `required` markers.
- `create` validates `tenant_id` and `project_id` via Nebius IAM APIs before optional wizard phases.
- Interactive `component add` and `component remove` use separate infra/apps selection prompts and an explicit confirmation before editing `config.yaml`.
- Repeating the same infra component id creates a new `instance_id`; explicit selectors such as `mk8s@training-cluster` are the canonical way to keep two clusters or two modules of the same type distinct in one project. For target-bound app charts, `<app-id>@<target-id>` selects the cluster target, and the same app id cannot be added twice to the same target.
- For source-backed modules, `inputs.parent_id`/`inputs.project_id` are pre-seeded from `client_info.nebius.project_id` when those variables are present.
- `component_sources.yaml` can declare per-component `defaults` so known Terraform inputs and Helm values are pre-seeded before prompting; literal defaults still appear in the interactive wizard as editable current values.
- `component_sources.yaml` can declare top-level `shared` values, and `defaults` entries can reference them with `shared.<path>` so shared values are resolved once and then materialized into component config blocks.
- `shared` is catalog-only; `config.yaml` must not declare a root `shared` block.
- The shipped public catalogs should contain only non-sensitive shared defaults. Project-scoped SSH public keys for jump-host modules belong in the private project `config.yaml`, not in `component_sources.yaml`; a private customer-local catalog may still expose `shared.admin_ssh.public_key` as a bootstrap seed that `create`/`component add` materialize into matching `inputs.ssh_public_key` fields.
- Shared-derived defaults are a create-time/component-add-time seeding contract only. Runtime commands do not backfill those values later; if an enabled row is missing a declared shared-derived target, validation fails and the project config must be corrected explicitly.
- For operator convenience, both `shared.admin_ssh.public_key` and per-project `inputs.ssh_public_key` accept inline `ssh-rsa` / `ssh-ed25519` values or readable local `.pub` file paths. `~` is expanded, relative paths resolve from the containing catalog/config file, runtime validation rejects unsupported key types, and persisted config/manifests are normalized back to inline key text.
- The bundled `mk8s` source entry sets `defaults.inputs.mk8s_cluster_public_endpoint: true`, and the built-in MK8s handoff resolves endpoint access dynamically from that input. If operators switch the control plane to private-only, local app operations still work as long as the machine running `nebius-cxcli` already has private network reachability to the MK8s API endpoint.
- The bundled `mk8s` source entry also sets `defaults.inputs.kube_network_service_cidrs: ["/20"]`. Nebius defaults omitted MK8s service CIDRs to `["/16"]`; on a single-pool `/16` subnet that can consume the entire pool and stall control-plane provisioning. `validate` and `deploy` now preflight that case against the live subnet before Terraform apply.
- The bundled `mk8s` source entry also sets `defaults.inputs.cpu_nodes_count: 2`, so the baseline CPU node-group size is visible in `config.yaml` and editable in the wizard instead of coming from an implicit Terraform module default.
- The bundled MK8s flow also treats effective node-group prerequisites as conditionally required: when the CPU baseline pool is enabled, `cpu_nodes_platform` / `cpu_nodes_preset` must be present unless the CPU override template supplies them, and when `gpu_enabled=true`, the wizard plus strict validation require `gpu_node_groups`, `gpu_nodes_count_per_group` unless GPU autoscaling override is configured, and effective GPU platform/preset values.
- `component_sources.yaml` can declare consumer-side `input` bindings so component outputs feed other component inputs without adding hardcoded wiring to the CLI.
- Interactive field prompting now offers all discoverable required and optional component fields for newly selected components.
- Per-component field phases default to `y` for infra modules and `n` for app charts, because Helm/chart defaults still cover the app case unless the operator explicitly wants overrides.
- Required fields are labeled `required` and must receive a valid value before the wizard advances unless the operator backs out or stops the wizard.
- Optional fields are labeled `optional`; blank answers keep defaults/current values and leave the field implicit in `config.yaml` when the value still matches a virtual module/chart default.
- Declared `wizard` metadata targeting `inputs.*` or `values.*` remains promptable even before that leaf exists in the payload; the wizard treats those paths as create-on-write prompt targets instead of warning that the path is missing.
- Fields grouped behind a sibling `<prefix>_enabled` toggle are prompted only when that toggle is true; enabling the toggle during the wizard appends the dependent fields later in the same run.
- Deferred dependency-prompt expansion must capture the current component's module metadata and required leaf set when the callback is queued, so later prompt expansion cannot accidentally read loop state from a different component iteration.
- Empty optional complex defaults such as `{}` and `[]` are presented with a blank prompt default plus explicit “blank keeps current empty map/list” text, instead of rendering those literals as inline prompt defaults.
- When Terraform module metadata falls back to local `variables.tf` parsing, multiline default values such as map/object literals must still be parsed as full defaults so the interactive wizard does not emit truncated prompt values.
- If a selected module has no catalog default for a required field, `create` prompts for it and stores it in the per-project `config.yaml`. That is the canonical path for sensitive per-project values such as jump-host `ssh_public_key`, even when the operator enters a local `.pub` path that is immediately normalized to inline key text.
- Wizard option sources are inferred by field conventions and resolved live via Nebius APIs when available.
- When a live provider-backed option lookup fails, the CLI prints a field-specific warning immediately before prompting that field manually and explains whether blank input is still acceptable.
- Explicit CLI severity diagnostics use fixed terminal colors: warnings are amber and errors are red.
- Optional provider-backed fields accept blank/skip answers as “leave unset” without revalidating that blank value against the live option list.
- Built-in Nebius provider option sources include:
  - `mk8s_compatible_platforms`
  - `mk8s_gpu_stack_presets`
  - `compute_platforms`
  - `compute_platform_presets`
  - `compute_public_image_families`
  - `project_subnets`
  - `project_networks`
  - `tenant_projects`
  - `mk8s_control_plane_versions`
- The bundled `mk8s` catalog uses that contract directly: `inputs.subnet_id` is wired to `project_subnets`, `inputs.k8s_version` uses the live MK8s control-plane version lookup with the first returned version auto-selected by default, platform fields use the MK8s compatibility lookup intersected with project compute-platform inventory, preset fields are chained to the selected live compute platform, and `inputs.cpu_nodes_os`, `inputs.gpu_stack_preset`, and `inputs.gpu_nodes_os` come from the same live MK8s compatibility matrix with catalog preference ordering.
- The bundled `vm` profile applies the same project-scoped lookup pattern for `inputs.subnet_id`, `inputs.platform`, and `inputs.preset`, resolves `inputs.source_image_family` from the live Nebius public image inventory for the selected platform and region using catalog preference ordering, adds a guided static choice for `inputs.public_ip_mode`, and reuses the existing InfiniBand fabric provider wiring for optional GPU-cluster VM shapes. That shared compute-platform provider path is also where the interconnect guidance now lives: single-GPU GPU presets stay Ethernet-only testing/dev shapes, clusterable multi-GPU presets are the InfiniBand / GPUDirect-RDMA path, live Capacity Dashboard advice ranks the platform -> region -> preset choices when tenant context exists, and stale VM fabric selections are cleared during interactive edits when a later preset/platform change no longer supports GPU clustering.
- This intentionally follows the public Nebius Compute contract in [Types of virtual machines and GPUs](https://docs.nebius.com/compute/virtual-machines/types#presets-compatible-with-gpu-clusters): cxcli asks the live project for supported platforms/presets first, then uses the selected preset's live `allow_gpu_clustering` metadata as the source of truth for GPU-cluster eligibility. The public doc currently lists the supported cluster-compatible 8-GPU presets, but cxcli does not freeze that list in code.
- The bundled jump-host profiles apply the same pattern at a simpler scope: `inputs.subnet_id` is project-scoped, `inputs.platform` comes from the live compute-platform inventory, and `inputs.preset` is chained to the selected platform.
- Optional wizard navigation uses a single control model across component selection, component phase prompts, and field prompts: `q` backs up to the previous wizard step so the operator can revise earlier answers, and `qq` stops the wizard immediately while preserving the current config payload. Guided nested prompts, such as the MysteryBox Secret/key loop, consume `q` locally until there is no earlier nested question left, then hand back to the outer field wizard. In TTY list and checkbox prompts, those controls are key shortcuts rather than selectable Back/Quit rows. At the first wizard step, `q` opens an explicit exit confirmation instead of trapping the operator on the same prompt. Remaining fields keep defaults when skipped.
- Stopping the wizard never discards the current project config edit. `create` and `component add` persist the current payload and warn only when required fields remain unresolved; if only optional fields are skipped, no warning is emitted.

## Observability

### Nebius Platform Model

Nebius observability has three public services:

- Monitoring stores metrics and exposes them through the web console, Prometheus-compatible APIs, and Grafana-compatible read paths.
- Logging stores logs and exposes them through the web console, Loki-compatible APIs, the Nebius CLI, and Grafana-compatible read paths.
- Tracing stores traces and exposes them through OpenTelemetry write APIs plus Tempo-compatible read paths.

Nebius service telemetry is separate from cxcli-managed collectors. The current
[supported-services page](https://docs.nebius.com/observability/services) lists
Monitoring metrics for Compute VMs/volumes, MK8s, Object Storage, MLflow, and
Managed PostgreSQL, and Logging for Compute serial logs, MLflow, Managed
PostgreSQL, MK8s, and MK8s applications. cxcli models those service-side
metric/log domains as source catalog buckets.

Nebius also has two agent families with different responsibilities:

- The Monitoring agent runs on Compute virtual machines and Managed Kubernetes node VMs. It is preinstalled by Nebius, collects system metrics, and can also forward journald logs from systemd services when the supported VM labels are enabled.
- Nebius Observability Agent for Kubernetes is a Helm chart installed into a Managed Kubernetes cluster. The detailed public product page documents logs, metrics, and traces for this agent, and that is the contract cxcli follows for cluster observability.

cxcli keeps those two agents separate on purpose. The Monitoring agent is a Nebius platform concern on Compute resources, while the Kubernetes agent is an explicit cluster workload owned by the project config and rendered bundle.

### cxcli Design Principles

cxcli's observability design follows these rules:

- Keep Nebius-managed control surfaces authoritative. If Nebius already provides a managed agent path, cxcli does not add a competing installer flow.
- Keep `config.yaml` project-facing. The operator should set signal toggles and a small number of product-facing knobs, not raw Helm values, raw OpenTelemetry configs, raw Compute labels, or static tokens.
- Keep auth public-safe. `config.yaml`, `component_sources.yaml`, and generated manifests must not carry static observability secrets.
- Keep multi-target behavior explicit. When one project has multiple MK8s clusters, collector rows and Flux output are target-scoped instead of relying on one implicit kubeconfig context.

### Current cxcli Workflow

The implemented workflow has five boundaries. Each boundary owns a different
part of the observability contract.

1. Catalog authorship.
   `component_sources.yaml` owns stable source facts: which reusable infra
   modules and Helm charts exist, what source locator/version they use, which
   chart defaults are unconditional, and which Grafana dashboard sources should
   be deployed. `component_cli_settings.yaml` owns cxcli behavior for those same
   component ids: signal defaults, Observability read/write endpoint records,
   Grafana datasource and dashboard signal bindings, app-side metric targets
   such as DCGM Exporter, and deploy-time guardrails. The files are joined by
   matching `components.<infra|apps>.<component-id>` paths, then parsed into
   typed component objects; settings for unknown component ids fail validation
   instead of being silently ignored. Endpoint records are global settings under
   `observability.endpoints.<read|write>` because Nebius Observability read/write
   APIs are tenant/project surfaces, not MK8s-owned or VM-owned resources. Each
   record owns the endpoint key, report label, URL/template text,
   `include_when` selectors, and optional bucket placeholder expansion. Grafana
   read-side metadata is split deliberately:
   `component_cli_settings.yaml` `components.apps.grafana.cli.datasources`
   declares the display names, stable UIDs, types, default marker, and
   Observability read endpoint keys that cxcli provisions into Grafana;
   `logout-timeout` sets Grafana's idle auth-session duration and defaults to
   `20m`; `component_sources.yaml`
   `components.apps.grafana.defaults.values.dashboards.*` declares each
   dashboard source and the datasource name it is bound to; and
   `component_cli_settings.yaml` `components.apps.grafana.cli.dashboard_signals`
   binds each observability signal to one existing `<folder>/<dashboard>` reference
   under `values.dashboards.*`. Each dashboard default must declare a datasource
   under `components.apps.grafana.cli.datasources` plus either a Grafana.com
   `gnetId` with pinned `revision` and imported `uid`, or dashboard JSON with a
   top-level `uid`.
   Bundled cxcli-owned dashboards use `json_file` package assets so the source
   catalog carries stable bindings without embedding large dashboard documents
   inline. User-maintained catalogs can also reference dashboard JSON with
   `json_file`; relative paths resolve from the active `component_sources.yaml`
   directory, and absolute paths are accepted for operator-managed files.
2. Customer intent.
   `create`, `component add`, and direct `config.yaml` edits write only
   customer intent under `deploy.targets[].observability.*` for MK8s and
   `deploy.observability.vm.*` for VMs. The MK8s wizard prompts that
   target-scoped contract directly. When those answers make the bundled
   `nebius-observability-agent` required, cxcli auto-selects the app immediately
   and emits the adjustment while the operator is still answering
   `deploy.targets[].observability.*` prompts. The later app prompt only asks whether to
   customize chart values; answering `n` keeps the app selected with defaults.
3. Normalization.
   Every config load normalizes the deploy contract before runtime validation.
   The current sequence is:
   `ensure_mk8s_gpu_app_rows`,
   `materialize_mk8s_gpu_app_values`,
   `normalize_observability_project_settings`,
   `ensure_observability_app_rows`, then
   `materialize_observability_infra_values`, then
   `strip_observability_generated_app_values`.
   This is why a direct config edit that sets
   `deploy.targets[].observability.enabled=true` or adds another GPU-enabled MK8s target
   still gets the required target-bound app rows and VM/MK8s infra-side
   materialization without a separate day-2 command, while source `config.yaml`
   stays focused on operator intent instead of generated collector scrape rules.
4. Render and deploy materialization.
   `render` and deploy paths re-run infra/app materialization before writing
   generated artifacts. MK8s materialization writes managed
   `values.config.*` and cxcli-owned `additionalTargets` into each target-bound
   collector chart row and preserves target scoping. VM materialization writes
   the supported journald labels or,
   for the standalone collector path, hidden VM module inputs. For GPU-enabled
   Nebius-image clusters with Kubernetes metrics enabled, cxcli also
   materializes the catalog-owned DCGM node-label policy into the MK8s node
   group overrides.
5. Deploy-time reconciliation and reporting.
   During deploy, after the cluster handoff is ready and before Flux applies
   app charts, cxcli reconciles the same catalog-owned DCGM node labels onto
   already-running GPU nodes when that policy is active. `write_inventory`
   then writes `generated/inventory/deploy-report.md` from the normalized
   runtime config and validation metadata.

The generated deploy report is the customer handoff for read-side tools. It
includes three observability sections when the selected signals require them:

- `Client`: the client name, tenant, project, and region from `config.yaml`.
- `Infra`: grouped into `Component Status` and `MK8s Clusters`. Cluster rows
  are nested so the target `instance_id`, configured cluster name, node shapes,
  fabric/public endpoint choice, and, after Terraform state is available, the
  Nebius MK8s cluster ID plus the derived kube context used by deploy/Flux
  commands stay together.
- `Apps`: grouped into platform apps, observability apps, and workloads so
  operator-facing app state does not mix observability collection state with
  workload URLs.
- `Observability Endpoints`: project-scoped datasource base URLs and regional
  write URLs, including concrete Prometheus federation URLs for service buckets
  that apply to the selected/deploy-created resources. Datasource base URLs are
  kept visible for external tools, but the report does not include raw API probe
  URLs by default.
- `Grafana`: grouped per target. Each target subsection contains the live
  bundled Grafana URL, bundled-dashboard links, target cluster ID/kube context
  metadata when available, and the
  target-scoped `kubectl --context=...` admin-password command. A separate
  `Notes` subsection explains pending runtime links, datasource provisioning,
  the Prometheus datasource split, and bundled-dashboard ownership.

The report intentionally does not write credentials. Operators supply
`Authorization: Bearer <observability static token or IAM token>` out of band,
using a service account or IAM token with project observability read access for
Grafana and other read-side tools.

### Grafana Dashboards

The bundled Grafana contract is the binding chain:

1. `component_cli_settings.yaml` `observability.endpoints.read.<key>` declares a Nebius read endpoint.
2. `component_cli_settings.yaml` `components.apps.grafana.cli.datasources.<id>` binds one Grafana
   datasource name/UID/type to that read endpoint key.
3. `component_sources.yaml` `components.apps.grafana.defaults.values.dashboards.<folder>.<dashboard>`
   binds a dashboard source to one Grafana datasource name.

`components.apps.grafana.cli.dashboard_signals.<signal>` is not a second
dashboard registry. It selects signal-bound dashboards for validation and
runtime status; `deploy-report.md` lists the cxcli-owned bundled dashboards
directly instead of separate Metrics, Logs, or Traces shortcut rows.

The binding exists because a dashboard only fits a datasource when that
datasource exposes the metric names, label keys, log labels, or trace search
surface used by the dashboard's queries. Grafana variables are used for values
such as selected node, namespace, pod, or service; they are not used as a
substitute for incompatible schema. If a Prometheus datasource has
`kubernetes_io_hostname` but not `node` on a metric, the dashboard query must
use `kubernetes_io_hostname` for that metric. If Loki stores Kubernetes labels
as `k8s_namespace_name` and `k8s_pod_name`, the dashboard must query those
labels instead of `namespace` and `pod`.

The bundled Kubernetes dashboards are cluster-aware because Nebius read
endpoints are project-scoped. The Metrics dashboard uses the `Nebius User
Metrics` Prometheus `k8s.cluster.id` label in quoted-label PromQL selectors and
exposes it as a `Cluster` variable. The GPU dashboard uses the `Nebius Services`
Prometheus `mk8s_cluster_id` label for DCGM metrics and builds its GPU-node
selector with `query_result(...)`, not `label_values(...)`, so stale project-wide
label metadata cannot list deleted or replaced nodes. The Logs dashboard uses
the Loki `k8s_cluster_id` label for the same variable. The bundled Traces
dashboard stays generic because live Tempo resource attributes depend on the
emitting workload and are not currently normalized to a required cluster label
by cxcli. When `deploy`, `flux apply`, or `flux bootstrap` has the target MK8s
cluster ID from the handoff Terraform output, generated Grafana report links
include `var-Cluster=<cluster-id>` so target-specific Metrics and Logs links open
with the matching cluster selected.
The files under
`generated/grafana_dashboards/<target-id>/...` still remain deploy-artifact
copies; cluster selection is a dashboard variable and URL parameter, not a
filesystem folder.

Catalog shape:

```yaml
observability:
  endpoints:
    read:
      metrics_user_read:
        template: https://read.monitoring.api.nebius.cloud/projects/{project_id}/prometheus

components:
  apps:
    grafana:
      cli:
        datasources:
          user-metrics:
            name: Nebius User Metrics
            uid: nebius-user-metrics
            type: prometheus
            read_endpoint: metrics_user_read
        dashboard_signals:
          metrics: nebius-kubernetes/kubernetes-cluster-monitoring
      defaults:
        values.dashboardProviders:
          dashboardproviders.yaml:
            apiVersion: 1
            providers:
              - name: nebius
                folder: Nebius
                folderUid: nebius
                type: file
                options:
                  path: /var/lib/grafana/dashboards/nebius
              - name: nebius-kubernetes
                folder: Nebius Kubernetes
                folderUid: nebius-kubernetes
                type: file
                options:
                  path: /var/lib/grafana/dashboards/nebius-kubernetes
        values.dashboards:
          nebius:
            nebius-disk:
              gnetId: 23425
              revision: 2
              uid: nebius-disk-user-stats
              datasource: Nebius Services
          nebius-kubernetes:
            kubernetes-cluster-monitoring:
              datasource: Nebius User Metrics
              json_file: grafana_dashboards/kubernetes-metrics.json
            kubernetes-gpu:
              datasource: Nebius Services
              json_file: grafana_dashboards/kubernetes-gpu.json
          myfolder:
            kubernetes-mylogs:
              datasource: Nebius Logs
              json_file: ./myk8slogs-dash.json
```

Ownership rules:

- The bundled `grafana` app declares its read-side contract under
  `components.apps.grafana.cli` in `component_sources.yaml`.
  `admin` owns the runtime admin username plus Secret name/keys, and
  `read_token` owns the Observability read-token Secret name/key and Grafana
  environment variable. cxcli issues that key for an ensured service account
  with the `viewer` role and stores the token only in the runtime Kubernetes
  Secret named by this catalog record. `datasources` owns the Grafana display name, UID,
  datasource type, default marker, read endpoint key, and report-facing
  description. `orgId` and
  `explore_queries` own generated signal-link org selection and fallback Explore
  queries. `values.dashboards` owns the dashboard source list. Each dashboard
  entry owns the datasource display name plus either a Grafana.com `gnetId` with
  pinned `revision` and imported dashboard `uid`, or dashboard JSON with a
  top-level `uid`. `dashboard_signals` owns only signal-bound dashboard
  selection by pointing each signal at one already declared
  `values.dashboards.<folder>.<dashboard>` entry. Bundled cxcli-owned dashboards
  are referenced with `json_file`. Source `config.yaml` does not store those
  dashboard JSON payloads; `render` writes them into `generated/` and points the
  generated Grafana HelmRelease at a generated ConfigMap.
- Dashboard sources are either:
  - upstream Grafana.com imports, declared with `gnetId`, pinned `revision`,
    imported dashboard `uid`, and `datasource`
  - cxcli-owned dashboard JSON package assets under
    `src/nebius_cxcli/grafana_dashboards/`, declared with `json_file` and
    `datasource`
  - operator-owned dashboard JSON files declared with `json_file` and
    `datasource` in a custom active component-sources file. Relative
    `json_file` paths resolve from that component-sources file's directory;
    absolute paths are accepted. Keep these user files outside `src/` unless
    they are intended to be shipped inside the `nebius_cxcli` Python package.
- cxcli does not dynamically generate or rewrite dashboards to fit a live
  datasource schema. The bundled cxcli-owned dashboards are fixed package JSON
  assets, and the bundled upstream service-dashboard example is a fixed pinned
  Grafana.com import. The datasource binding determines where each dashboard
  queries; validation checks that the fixed dashboard source fits the bound read
  endpoint.
- `component_sources.yaml` should not carry large inline cxcli dashboard JSON.
  The source catalog names the dashboard file and datasource; the settings
  catalog names dashboard signal bindings, datasources, and read endpoints. Package data
  carries the actual dashboard JSON.
- A Grafana Helm chart provider key must use one dashboard delivery mechanism:
  chart-managed `values.dashboards` imports or `dashboardsConfigMaps`, not both.
  The bundled catalog therefore keeps the single Nebius service-dashboard import
  example under the `nebius` provider key and cxcli-owned Kubernetes JSON
  dashboards under the `nebius-kubernetes` provider key. This is a Helm
  chart/provider separation, not a per-cluster split; per-cluster selection stays
  in dashboard variables and generated dashboard URL parameters.
- Every cxcli-owned dashboard JSON must be a Grafana dashboard object with a
  stable top-level `uid`. For cxcli-owned dashboards the UID lives inside the
  JSON package asset. For upstream Grafana.com imports the UID is copied into
  the catalog from the pinned `gnetId` revision. That UID is the update identity
  used by Grafana imports and the lookup identity used by `validate-dashboards`.
- Dashboard JSON should reference the Grafana datasource UID in panel targets
  and variables. The chart default still declares the human Grafana datasource
  name because the Grafana Helm chart uses that field for dashboard imports and
  Grafana.com dashboard substitutions.
- `service-metrics` provisions the `Nebius Services` Prometheus datasource from
  the `metrics_service_provider_read` endpoint key. That endpoint renders to
  `https://read.monitoring.api.nebius.cloud/projects/<project-id>/service-provider/prometheus`
  and reads Nebius/provider service metrics. This metric domain includes
  Nebius-managed service telemetry, platform/node-style metrics, GPU/DCGM
  metrics exposed through the service-provider path, and other metrics owned by
  Nebius service integrations.
- `user-metrics` provisions the `Nebius User Metrics` Prometheus datasource from
  the `metrics_user_read` endpoint key. That endpoint renders to
  `https://read.monitoring.api.nebius.cloud/projects/<project-id>/prometheus`
  and reads customer/user-ingested Prometheus metrics. For cxcli-managed MK8s
  observability, this is where Kubernetes API server, cAdvisor/container,
  namespace, pod, and workload-style metrics from the Nebius observability agent
  are read back.
- The two Prometheus datasources are not duplicates and are not a single
  automatic aggregation layer. They are separate server-side read views into
  different metric domains. PromQL aggregation still happens only when the
  dashboard query asks for it with expressions such as `sum by (...)`,
  `avg by (...)`, or `rate(...)`. The generated `deploy-report.md` renders this
  split from the settings-owned datasource descriptions so operators can see why
  the service-dashboard example, the Kubernetes GPU dashboard, and Kubernetes
  workload dashboards use different Prometheus datasources.

Dashboard source materialization workflow:

1. Author or update the dashboard JSON under
   `src/nebius_cxcli/grafana_dashboards/` when the dashboard is cxcli-owned and
   must ship inside the Python wheel. Use deterministic `uid` values such as
   `cxcli-kubernetes-metrics`, `cxcli-kubernetes-logs`, and
   `cxcli-kubernetes-traces` so rerenders update the same Grafana dashboards
   instead of creating duplicates. For customer/operator-owned dashboard JSON,
   keep files next to the custom `component_sources.yaml` or in another
   operator-owned directory and reference them with relative or absolute
   `json_file` paths; do not put customer files under package `src/`.
2. Point the catalog dashboard default at the asset with `json_file` and declare
   the intended Grafana datasource name with `datasource`.
3. If the dashboard should be used as a Metrics, Logs, or Traces signal binding,
   bind the observability signal in `component_cli_settings.yaml` by setting
   `components.apps.grafana.cli.dashboard_signals.<metrics|logs|traces>` to
   `<folder>/<dashboard>`. Dashboards that are not signal-bound are still
   deployed and validated as catalog dashboard sources. If they are cxcli-owned
   package assets under `src/nebius_cxcli/grafana_dashboards/`, they also appear
   in the generated `deploy-report.md` bundled-dashboard list for each Grafana
   target. Operator-owned external dashboard JSON is imported into Grafana but
   intentionally omitted from that handoff shortcut list.
4. `load_component_sources()` resolves the `json_file` relative to the
   explicit or discovered component-sources file, resolves absolute
   `json_file` paths directly, and then falls back to packaged cxcli resources
   for bundled assets such as `grafana_dashboards/kubernetes-metrics.json`. It
   parses the dashboard JSON, requires a top-level `uid`, writes the JSON into
   the in-memory Helm values as `json`, and removes `json_file` from the
   runtime chart defaults. It rejects dashboards that declare both `json` and
   `json_file`.
5. `validate-sources` checks the static graph: every catalog dashboard source
   has a datasource declared under `component_cli_settings.yaml`
   `components.apps.grafana.cli.datasources`,
   every Grafana.com import has `gnetId`, pinned `revision`, and imported
   `uid`, every cxcli-owned dashboard has JSON with a top-level `uid`, every
   dashboard signal binding points to an existing dashboard source, and every datasource
   `read_endpoint` exists under `observability.endpoints.read`.
6. `render` keeps the operator-facing `config.yaml` clean and writes cxcli-owned
   dashboard JSON as deployable generated artifacts:
   `generated/grafana_dashboards/<target-id>/<folder>/<dashboard>.json` for the
   readable JSON copies, plus
   `generated/flux/targets/<target-id>/configmap-grafana-<folder>-dashboards.yaml`
   for the ConfigMap that Grafana imports. The generated HelmRelease uses
   `dashboardsConfigMaps.<folder>` for cxcli-owned dashboard providers and keeps
   only Grafana.com `gnetId` imports under chart-managed `values.dashboards`
   providers. A single provider key is never rendered with both mechanisms.
7. `deploy`, `flux apply`, and `flux bootstrap` create or reuse the Grafana
   admin Secret and Observability read-token Secret, refresh the read token when
   a catalog-bound Prometheus read endpoint clearly rejects it, apply the
   Grafana HelmRelease, set Grafana's public `root_url` from the discovered
   Gateway/LoadBalancer address, and builds direct deploy-report links for every
   active catalog dashboard whose JSON matches a packaged
   `src/nebius_cxcli/grafana_dashboards/*.json` asset. When the target MK8s
   cluster ID is known, those bundled links include `var-Cluster=<cluster-id>`;
   the links are shown as pending until the target Grafana base URL is known.
8. Grafana imports dashboards asynchronously from the chart-rendered dashboard
   ConfigMap. Until the target Grafana base URL is known, report generation
   marks bundled dashboard links as pending rather than adding signal-specific
   Explore fallbacks.
9. `validate-dashboards <config.yaml>` checks the live post-deploy state through
   the bundled Grafana API for every catalog dashboard source. It confirms the
   datasource UID/type exists, warns if the expected dashboard UID has not been
   imported yet, and validates the dashboard query contract through Grafana
   datasource proxy requests when dashboard JSON is available from a cxcli-owned
   package asset or the live imported Grafana.com dashboard. It shows a timed
   dashboard-level spinner/progress display while querying live Grafana.
   Target-scoped rows must resolve an explicit kube context. The current
   kubeconfig context is accepted only when its generated Nebius name matches the
   target; otherwise the command fails fast instead of using an unrelated
   ambient `kubectl` current context. It does not mutate, regenerate, or repair
   dashboard JSON.

Current bundled package dashboards:

- Metrics: `nebius-kubernetes/kubernetes-cluster-monitoring` binds to `Nebius User
  Metrics` and `metrics_user_read`. It uses `container_cpu_usage_seconds_total`
  for cluster/node discovery, CPU cores, and container counts;
  `container_memory_working_set_bytes` for memory working set and pod counts;
  and `container_network_receive_bytes_total` /
  `container_network_transmit_bytes_total` for node-level network throughput.
  Node selectors use `query_result(...)` with `kubernetes_io_hostname` so the
  dropdown comes from current query results rather than a stale label index.
- GPU: `nebius-kubernetes/kubernetes-gpu` binds to `Nebius Services` and
  `metrics_service_provider_read` because the Nebius monitoring-agent/DCGM
  service metrics are exposed through the service-provider read endpoint. It
  filters by `mk8s_cluster_id`, lists GPU nodes from current
  `DCGM_FI_DEV_GPU_UTIL` query results, reports GPU count, and keeps the main
  utilization, memory used, power, temperature, and clock time-series per GPU
  UUID. Time-series legends start with the GPU UUID and include `instance_id` as
  node context.
- Logs: `nebius-kubernetes/kubernetes-logs-from-loki` binds to `Nebius Logs` and
  `logs_loki_read`. It queries the `default` bucket and uses
  `k8s_namespace_name` plus `k8s_pod_name` variables.
- Traces: `nebius-kubernetes/kubernetes-traces` binds to `Nebius Traces` and
  `traces_tempo_read`. It uses a generic TraceQL `{}` search so the dashboard is
  valid before workloads emit application-specific trace attributes. A live
  validation warning that no traces were returned means the endpoint is
  reachable but no trace data matched the selected time window.

Live fit validation rules:

- Prometheus validation extracts metric names and required label keys from
  dashboard variables and panel expressions, checks `/api/v1/series` through the
  Grafana datasource proxy, and runs representative `/api/v1/query` checks with
  Grafana interval variables replaced by concrete durations for report
  dashboards. Target-scoped selectors are narrowed with `k8s.cluster.id` for
  user-ingested Kubernetes Prometheus metrics and `mk8s_cluster_id` for Nebius
  service-provider GPU/DCGM metrics when those labels are present. Non-report
  upstream Grafana.com dashboards can be resource-specific and noisy, so cxcli
  checks their datasource/import/metric-label discovery and summarizes missing
  metric series as warnings instead of executing every panel query.
- Loki validation extracts selector labels from variables and LogQL, discovers
  labels both globally and inside `{__bucket__="default"}`, then runs
  representative `query_range` checks through the Grafana datasource proxy.
- Tempo validation discovers TraceQL tags when the endpoint exposes them and
  runs representative TraceQL searches. Missing trace data is a warning, not a
  schema error, when the endpoint itself is reachable.
- External Grafana instances, external Prometheus configs, LogCLI profiles, and
  dashboard designs outside the bundled Grafana app remain operator-owned.
  cxcli validates every bundled Grafana dashboard source because those are the
  dashboards it renders and imports into the customer handoff. Signal-bound
  dashboard sources are still validated, but `deploy-report.md` lists bundled
  dashboards directly instead of adding Metrics/Logs/Traces shortcut rows.

### Source And Settings Catalog Contract

Observability is split across the same catalog pair:

- `component_sources.yaml` declares component sources and Grafana dashboard source entries under `components.apps.grafana.defaults.values.dashboards.*`.
- `component_cli_settings.yaml` declares cxcli behavior and read/write endpoint bindings.

`component_cli_settings.yaml` is the authoritative cxcli-owned observability settings registry:

- `observability.endpoints.*` defines the tenant/project-wide Nebius Observability
  read/write endpoint templates used by reports, Grafana datasource bindings,
  and collector guidance:
  - `endpoints.write.*` covers public Monitoring, Logging, and Tracing ingest
    endpoints plus platform-managed write notes
  - `endpoints.read.*` covers public Prometheus, Loki, Tempo, and federation
    read endpoints
  - endpoint records are global because those APIs are reusable by MK8s, VM,
    Object Storage, PostgreSQL, and future Nebius resource types
- `components.infra.mk8s.cli.observability.*` defines the Kubernetes-agent contract:
  - `primary_agent.kind: kubernetes_agent`
  - `primary_agent.chart_component_id`
  - `primary_agent.{logs,metrics,traces}` keep the customer-facing signal defaults
  - `primary_agent.validation` is a boolean switch for the deploy-time
    Observability Agent guardrail; it defaults to enabled when omitted, while
    cxcli keeps the Nebius-agent object names, value paths, selectors, and
    bounded check limits internal
  - `service_metrics.buckets` and `service_logs.buckets` declare the Nebius-managed service metric/log domains that exist for the cluster itself
- `components.infra.vm.cli.observability.*` defines the VM Monitoring-agent contract:
  - `primary_agent.kind: monitoring_agent`
  - `primary_agent.metrics` records the built-in VM metrics path
  - `primary_agent.logs` keeps the VM journald collection defaults
  - `service_metrics.buckets` and `service_logs.buckets` declare the automatic Compute metric domains and Compute serial-log bucket
  - default-off `public_ingest.*` defaults for public write-side ingest, including the collector package source and Prometheus companion package
- Other Nebius service components use the same `cli.observability.service_metrics.buckets`
  and `cli.observability.service_logs.buckets` shape without pretending to own
  an agent. In the bundled catalog, Object Storage declares the `sp_storage`
  service-metrics bucket, Managed PostgreSQL declares the `msp` metrics bucket
  and `sp_postgres` log bucket, and Object Storage request logs stay documented
  as Audit Logs rather than a Loki bucket.
- `components.apps.<id>.cli.observability.metric_targets` is the app-side settings-owned place for metrics endpoints or prerequisites when cxcli must reason about them. In the bundled catalog this is how cxcli tracks the GPU Operator DCGM Exporter source through `discovery.*` metadata and the Nebius-specific GPU node policy required to make that source actually run on Nebius driverful nodes.

Each endpoint record has this shape:

```yaml
observability:
  endpoints:
    read:
      metrics_user_read:
        label: Metrics read (Prometheus, user-ingested metrics)
        template: https://read.monitoring.api.nebius.cloud/projects/{project_id}/prometheus
        include_when:
          - kubernetes_metrics
          - vm_standalone_metrics
    write:
      metrics_prometheus_remote_write:
        label: Metrics write (Prometheus Remote Write)
        template: https://write.monitoring.{region}.nebius.cloud/projects/{project_id}/prometheus/api/v1/write
        include_when:
          - kubernetes_metrics
          - vm_standalone_metrics
```

The endpoint key is the stable binding handle. Grafana datasources refer to
that key with `read_endpoint`; reports use `label`; endpoint rendering uses
`template`; and `include_when` selects the endpoint from computed deployment
signals such as `kubernetes_metrics`, `vm_standalone_logs`, `logs`, or
`metrics`. A future read endpoint can be added by declaring a new
`observability.endpoints.read.<key>` record and binding a Grafana datasource to
that key.
Python owns only signal evaluation and materialization, not the endpoint
allowlist.

Service metric/log bucket records have this shape:

```yaml
service_metrics:
  buckets:
    sp_storage:
      label: Object Storage service metrics
service_logs:
  buckets:
    sp_postgres:
      label: Managed Service for PostgreSQL logs
```

The bucket key is the value used in service-provider Prometheus federation URLs
or the Loki `__bucket__` selector. `include_when` is optional and can refer to
generic component conditions such as `inputs.gpu_enabled`; if omitted, the
bucket applies whenever that component row is enabled. This keeps future Nebius
service bucket additions in `component_sources.yaml` instead of Python.

Important catalog choices:

- The MK8s cxcli-managed project contract is default-off, but once the operator enables it cxcli treats `collect_k8s_cluster_metrics=true` as the enabled baseline so cluster and node health are included by default. That differs from the public chart default of `false` and is an intentional cxcli opinion, not an attempt to mirror upstream defaults verbatim.
- cxcli pins `oci://cr.nebius.cloud/observability/public/nebius-observability-agent-helm` because the current Nebius Observability Agent for Kubernetes docs identify that OCI chart as the supported chart, the traces ingest workflow uses the same chart, and its rendered surface matches the logs+metrics+traces contract cxcli materializes. Ref: [Nebius Observability Agent for Kubernetes](https://docs.nebius.com/observability/agents/nebius-o11y-agent), [Nebius traces ingest](https://docs.nebius.com/observability/traces/ingest)

### Customer Config Contract

`config.yaml` exposes only the deploy-facing observability contract under `deploy`:

```yaml
deploy:
  targets:
  - instance_id: cluster1
    observability:
      enabled: false
      kubernetes:
        logs:
          enabled: true
          collect_agent_logs: false
          excluded_namespaces:
            - kube-system
        metrics:
          enabled: true
          collect_agent_metrics: false
          collect_k8s_cluster_metrics: true
          excluded_namespaces:
            - kube-system
        traces:
          enabled: true
  observability:
    vm:
      logs:
        enabled: true
        systemd_units: []
      collector:
        enabled: false
        metrics:
          enabled: true
        logs:
          enabled: true
          systemd_units: []
```

Design rules for the customer config:

- `deploy.targets[].instance_id` binds target-scoped deploy settings to an enabled MK8s cluster target.
- `deploy.targets[].observability.enabled` is the per-cluster switch for cxcli-managed MK8s observability.
- Nebius Monitoring/Logging/Tracing endpoints are project-scoped service surfaces. Deploy observability settings control whether cxcli deploys or configures producers against them; they are not the thing that makes the endpoint URLs exist.
- Nebius-managed service metrics/logs for enabled resources are represented by catalog bucket metadata, not by customer `deploy.observability.*` toggles. For example, PostgreSQL and Object Storage service metrics can appear in the report even when no cxcli-managed collector is enabled.
- `deploy.targets[].observability.kubernetes.*` is only for the MK8s Kubernetes-agent path.
- `deploy.observability.vm.logs.*` is only for the VM Monitoring-agent journald-label path.
- `deploy.observability.vm.collector.*` is the separate default-off cxcli-managed standalone VM collector path for public write-side journald logs and host metrics.
- The bundled VM catalog defaults `deploy.observability.vm.logs.enabled` to true, but that branch is active only when `deploy.observability.enabled=true`.
- `create` and normalization keep the contract scoped to the enabled infra set:
  - MK8s-only projects keep `deploy.targets[].observability.kubernetes.*`
  - VM-only projects keep `deploy.observability.vm.logs.*` plus `deploy.observability.vm.collector.*`
  - mixed projects keep both
- Unrelated project-scope branches are pruned instead of leaking into the customer config. For example, VM-only configs do not keep MK8s GPU deploy validations.

What cxcli intentionally does not put in `config.yaml`:

- static observability keys or tokens
- Grafana credentials or static tokens
- raw `values.config.iam.*` auth details for the Kubernetes chart
- raw Compute journald labels
- whole chart `values.yaml` trees

### Runtime Materialization

The source/settings catalog contract becomes runtime state during normalization and render:

- When `deploy.targets[].observability.enabled=true` for an MK8s component, cxcli ensures the bundled collector and Grafana chart rows exist for that target. The collector materializes target-facing toggles into chart-native `values.config.*`; Grafana materializes datasource provisioning for the selected Metrics, Logs, and Traces read endpoints.
- In multi-target projects, that materialization is target-scoped: each enabled MK8s target gets its own collector and Grafana rows with `instance_id` set to the target id.
- Grafana admin Secret values, read-token Secret/environment values, datasource values, fallback Explore queries, dashboard signal bindings, org ID, and the idle auth-session timeout are generated from the active settings catalog. Dashboard source values are generated from the active source catalog. Datasource URLs use the same settings endpoint records used by the deploy report. The bearer token comes from a deploy-time Kubernetes Secret exposed as an environment variable for Grafana provisioning. `Nebius Services` points at the service-provider Monitoring read endpoint; `Nebius User Metrics` points at the user-ingested Prometheus read endpoint because that endpoint contains the cxcli-managed Kubernetes agent metrics. Logs and traces use `Nebius Logs` and `Nebius Traces`. Catalog validation fails if any Grafana dashboard source lacks datasource metadata plus either `gnetId` with pinned `revision` and imported `uid` or dashboard JSON with a top-level `uid`, if a dashboard datasource name is not declared under `components.apps.grafana.cli.datasources`, if a dashboard signal binding references a missing dashboard source, or if a datasource read endpoint is not declared under the observability endpoint registry.
- The built-in VM Monitoring agent remains platform-managed whenever a `vm` component is enabled; cxcli does not install it and does not configure its internal metrics ingest path.
- When `deploy.observability.enabled=true` and a VM component is enabled, cxcli materializes the supported Compute labels into `infra.components[id=vm].inputs.labels`:
  - `nebius.o11y.systemd-logs-collection.enabled=true`
  - optional `nebius.o11y.systemd-logs-collection.units=<unit1;unit2>`
- When `deploy.observability.enabled=true` and `deploy.observability.vm.collector.enabled=true`, cxcli also materializes hidden VM module inputs that bootstrap the standalone collector:
  - catalog-defined collector package name/version
  - catalog-defined APT repository URL, key URL, suite, component, and origin
  - catalog-defined Prometheus companion package name
  - VM metadata token path
  - public regional write endpoints derived from `client_info.nebius.region_id`
  - loopback ports for the local metrics export and Prometheus agent
  - signal toggles and optional systemd-unit allowlist
- The standalone collector requires `infra.components[id=vm].inputs.service_account_id` so the VM metadata token can authenticate to the public write endpoints. The current first cut is intentionally narrow: module-managed Ubuntu-family boot disks only, host metrics plus journald logs only, and no attempt to become a generic arbitrary app log/metric shipper.
- Generated manifest and inventory/report output describe which observability path is active, which signals are enabled, and which public read/write endpoints apply to that project.

This materialization boundary is why `component_sources.yaml` and `config.yaml` can stay clean: catalog owns source facts, config owns project intent, and normalized runtime state bridges them.

### Signal Flows

Kubernetes logs:

- The Kubernetes agent collects workload logs from pod stdout/stderr and forwards them to Logging.
- The public docs describe this as default-on log collection for workloads, with the resulting logs landing in the `default` bucket.
- Workload-level opt-out remains a workload concern, not a cxcli config branch.

Kubernetes metrics:

- The Kubernetes agent collects Prometheus-style metrics through its scrape pipeline.
- `collect_k8s_cluster_metrics` controls cluster, node, and control-plane style metrics in that pipeline. cxcli treats this as the user-facing intent for Nebius Observability Agent scrape config in source `config.yaml`; during render it emits a cxcli-owned set of `additionalTargets` for API server, kubelet, cAdvisor, and Hubble scrapes with a small allowlist of stable labels such as `node` and `kubernetes_io_hostname`. The rendered chart value `config.metrics.collectK8sClusterMetrics` is set to `false` so the upstream chart's built-in kubelet/cAdvisor jobs do not copy every Kubernetes node label, including high-volume NFD feature labels, into every container metric.
- The bundled default excludes ordinary `kube-system` service/pod annotation scrapes for metrics, matching the agent namespace-exclusion model while leaving chart-owned infrastructure targets under the agent chart's control.
- App-side metric sources, such as the GPU Operator's DCGM Exporter service, are modeled through `metric_targets` metadata when cxcli needs catalog-owned prerequisites or reporting.
- Catalog-owned targets with `discovery.kind: prometheus_annotations` rely on the Nebius agent's built-in service/pod annotation discovery. Catalog-owned targets with `discovery.kind: additional_target` and cxcli-owned cluster metric jobs are rendered into the Nebius agent's chart-native `values.config.metrics.additionalTargets` list. User-defined `additionalTargets` on the chart row are preserved unless they reuse a catalog-owned `job_name`.
- On Nebius driverful GPU nodes, cxcli may also materialize node labels so only the needed GPU Operator observability operands run without duplicating the Nebius-managed device-plugin path.

Kubernetes traces:

- The Kubernetes agent exposes an in-cluster OTLP/gRPC receiver for traces at `nebius-observability-agent.<namespace>.svc.cluster.local:4317`.
- Applications send traces to that in-cluster service; the agent forwards them to Nebius Tracing.

VM metrics:

- The Monitoring agent collects VM and node metrics automatically.
- For standalone VMs this feeds the Console Metrics view and the Monitoring read endpoints.
- The built-in agent writes those metrics through Nebius-managed internal regional ingest. That path is not the same as the customer-facing public write endpoints used by external collectors or the MK8s Kubernetes agent.
- Managed Kubernetes node VMs also get the same platform metrics path automatically, but cxcli does not expose that as a second MK8s config branch.

VM standalone collector metrics:

- The default-off standalone collector uses the catalog-defined `nebius-o11y-agent` package on the VM to expose host metrics on loopback and a catalog-defined Prometheus agent companion to remote-write them to Monitoring.
- This is intentionally a customer-managed public-ingest path, separate from the built-in Monitoring agent.
- The current cxcli-managed metrics set is host-oriented and comes from the public `nodeexporterreceiver` path. It is meant to give customers one cross-resource dashboard path for VM and MK8s metrics, not to replace Nebius service metrics.

VM journald logs:

- The Monitoring agent can forward journald logs from systemd services when the supported VM labels are enabled.
- cxcli exposes that through `deploy.observability.vm.logs.*` only on the explicit `vm` component path.
- VM journald logs land in Logging as user-ingested logs and are read from the `default` bucket.
- When enabled, those logs also use the platform-managed Logging ingest path, not the public customer log-write endpoints.

VM standalone collector logs:

- The default-off standalone collector can also forward journald logs through the public Logging gRPC endpoint.
- cxcli uses the public `nebius-o11y-agent` package for the bundled default path and authenticates it with the VM metadata token, again keeping this separate from the built-in Monitoring-agent journald-label path. The VM module no longer hardcodes the package repository or companion package; cxcli materializes those fields from `components.infra.vm.cli.observability.public_ingest`.
- The same optional `systemd_units` allowlist concept exists here, but it applies to the standalone collector's own journald receiver config rather than Compute labels.

### Endpoints and Auth

cxcli keeps endpoints in the catalog and renders them into reports with placeholders such as `<project-id>` and `<region>`.
Those URLs are service-scoped project endpoints; the cxcli project switch decides whether collectors are configured to use them, not whether the URLs themselves exist.

Write endpoints relevant to the MK8s path:

- Monitoring OTLP metrics: `https://write.monitoring.<region>.nebius.cloud/projects/<project-id>/opentelemetry/v1/metrics`
- Monitoring Prometheus Remote Write: `https://write.monitoring.<region>.nebius.cloud/projects/<project-id>/prometheus/api/v1/write`
- Logging HTTPS ingest guidance for external collectors: `https://write.logging.<region>.nebius.cloud`
- Logging gRPC/DNS endpoint used by the bundled Kubernetes agent: `dns:///write.logging.<region>.nebius.cloud:443`
- Tracing OTLP/gRPC: `dns:///write.tracing.<region>.nebius.cloud:443`

Write endpoints relevant to the standalone VM collector path:

- Monitoring Prometheus Remote Write: `https://write.monitoring.<region>.nebius.cloud/projects/<project-id>/prometheus/api/v1/write`
- Logging gRPC/DNS: `dns:///write.logging.<region>.nebius.cloud:443`

Read endpoints:

- Nebius/provider service metrics: `https://read.monitoring.api.nebius.cloud/projects/<project-id>/service-provider/prometheus` (`service-provider` is literal). The bundled Grafana `Nebius Services` datasource uses this endpoint.
- Customer/user-ingested metrics: `https://read.monitoring.api.nebius.cloud/projects/<project-id>/prometheus`. The bundled Grafana `Nebius User Metrics` datasource uses this endpoint.
- Prometheus federation bucket URLs: `https://read.monitoring.api.nebius.cloud/projects/<project-id>/buckets/<bucket>/prometheus/federate`, where `<bucket>` is selected from catalog-declared service buckets that apply to the deployment, such as `compute`, `gpu`, `nbs`, `sp_storage`, and `msp`
- Loki-compatible logs: `https://read.logging.api.nebius.cloud/projects/<project-id>`
- Tempo-compatible traces: `https://read.tracing.api.nebius.cloud/projects/<project-id>/tempo`
- Direct API probes for reachability use tool-specific subpaths below those datasource URLs, for example `/api/v1/query?query=count(...)` or `/api/v1/query?query=up` for Prometheus, `/loki/api/v1/query?...` for Loki, and `/api/v2/search/tags` for Tempo. The generated report intentionally omits those raw probe URLs by default because the bundled Grafana links and datasource base URLs are the customer-facing handoff.

Auth model:

- The bundled Kubernetes agent keeps the public-safe Nebius-managed auth path:
  - `auth_scheme: iam-token-file`
  - token file: `/mnt/cloud-metadata/tsa-token`
  - IAM endpoint: `tokens.iam.api.nebius.cloud:443`
- The standalone VM collector also keeps auth public-safe:
  - VM metadata token file: `/mnt/cloud-metadata/token`
  - no static token in repo config
  - the attached VM service account owns the write-side permissions
- External collectors, `nebius logging`, Prometheus, LogCLI, or Grafana use `Authorization: Bearer <observability static token or IAM token>` supplied out of band.
- cxcli never asks the user to paste those secrets into `config.yaml`.
- For in-cluster Grafana, `deploy`, `flux apply`, and `flux bootstrap` create or reuse the target-cluster admin/password Secret and Observability read-token Secret before Helm reconciliation. If the read-token Secret is missing, cxcli ensures a project service account, grants `viewer` through a project IAM group, issues an `OBSERVABILITY` static key, and stores the one-time token only in that Kubernetes Secret.
- The generated deploy report renders client identity, infra inventory, and three user-facing observability surfaces: public write endpoints, public read endpoints, and Grafana links. The Grafana section lists every configured Grafana target, shows pending links until `deploy` or `flux apply` can read the target Gateway/LoadBalancer status, waits briefly for a newly created Gateway/LoadBalancer address, then reports the live URL, cxcli-owned bundled-dashboard links, target cluster ID/kube context metadata when available, and the target-specific `kubectl --context=...` command for retrieving the admin password. Direct read API probe URLs and duplicate dashboard shortcut rows are kept out of the default report to keep the customer handoff compact.

VM-specific note:

- For the built-in Monitoring agent path, cxcli still does not generate customer-configurable VM write-endpoint settings because Nebius owns that ingest path.
- The separate standalone collector mode is the one place where cxcli now owns a VM public write-endpoint contract, and it is intentionally explicit and default-off so it is not confused with the built-in Monitoring agent.

### Deploy-Time Guardrail

When an MK8s target has `deploy.targets[].observability.enabled=true` and the
effective Kubernetes signal contract requires the Nebius Observability Agent,
cxcli generates one target-scoped deploy validation with kind
`mk8s_observability_ingestion`.

- The guardrail is generated at `render` time into
  `generated/nebius-cxcli-manifest.json` under `deploy.validations[]`; it is not
  a user-facing `config.yaml` toggle.
- `deploy` runs the guardrail after Terraform apply, Flux apply, and Flux
  readiness for the selected target, using the same handed-off kubeconfig as
  the rest of target-scoped app work.
- The live checks are intentionally in-cluster. The HelmRelease condition must
  be true, the rendered Helm values for logs/metrics/traces must match the
  enabled signal contract, cluster metric collection must have rendered
  additional targets when `collect_k8s_cluster_metrics=true`, the agent
  DaemonSet must be Ready, and the OTLP/gRPC service must have a ready
  EndpointSlice when traces are enabled. The settings catalog exposes only the
  boolean `primary_agent.validation` switch; the Nebius Observability Agent
  object names, value paths, selectors, and bounded check limits are internal
  cxcli defaults.
- The guardrail is designed to stay fast on 1000-4000 node clusters. The pass
  path uses direct object reads for the HelmRelease, DaemonSet, and Service plus
  a bounded EndpointSlice list; it does not list every agent pod or every
  endpoint. A bounded non-running pod sample is collected only when the
  DaemonSet check fails.
- Results are written as
  `generated/inventory/observability-ingestion-report-<target>.json` and rolled
  into the `Validations` section of
  `generated/inventory/deploy-report.md`.
- This guardrail answers "is the in-cluster producer healthy and configured for
  the selected signals?" It does not replace `validate-dashboards`, which
  answers "do the live read endpoints and Grafana datasources contain the
  metrics, labels, log labels, trace reachability, and dashboard query contract
  expected by the bundled dashboards?"
- The one-run override flags remain operational escape hatches:
  `--skip-validations` skips every selected deploy validation, and
  `--skip-validation observability-ingestion` skips only this guardrail for the
  current `deploy` run without rewriting `config.yaml`.

### Operational Notes

- MysteryBox backend creation and Kubernetes secret sync are intentionally separate contracts. The `mysterybox` Terraform component creates Nebius MysteryBox secrets and keeps the product-native `inputs.secrets` list. Kubernetes sync is target-scoped under `deploy.targets[].secrets.mysterybox.*` and uses External Secrets Operator's native `nebiusmysterybox` provider, so those prompts are deploy-target settings for the MK8s target rather than MysteryBox Terraform module inputs. The MK8s wizard shows those sync prompts only when the Terraform `mysterybox` component is also selected and enabled; in that context the sync toggle defaults to `true` and accepting defaults persists `enabled: true`, `allow_all_namespaces: true`, and `sync_namespaces: [default]`. cxcli derives one full-sync `ExternalSecret` for each declared MysteryBox Secret in each sync namespace. Deploy resolves Terraform-created `mbsec-...` IDs from Terraform `secret_ids` output after Terraform apply, refreshes the Flux manifests, and only then applies ESO resources.
- The `payload_values` module input is runtime-only in cxcli-generated Terraform roots. Render declares a sensitive root variable such as `mysterybox_payload_values`, passes it to the child module, and omits it from generated tfvars and manifests; operators provide values at first Terraform/deploy time as a JSON/YAML two-level map keyed by secret name and payload key. Interactive local `deploy`, `terraform plan`, and `terraform apply` runs prompt with hidden input for missing first-deploy values before Terraform starts. CI and other non-interactive runs set `TF_VAR_mysterybox_payload_values`; non-default MysteryBox instances use their rendered module variable name, for example `TF_VAR_secretstore_alpha_payload_values`. cxcli preflight checks first-deploy Secrets whose `version_id` is empty or `n/a` and reports the exact missing entries before Terraform apply. After cxcli records the created `version_id` in source config, the generated manifest, and generated Terraform tfvars, later plan/apply/destroy runs do not need the original payload values. If Nebius creates the Secret versions but Terraform exits because the provider lost an operation poll, deploy best-effort recovers those `mbsecver-...` IDs from Terraform state and refreshes the generated bundle so the next deploy continues without asking for payload values again. `inputs.payload_values` in source config is rejected so payload cleartext cannot become part of `config.yaml` or generated artifacts.
- When the Terraform `mysterybox` component and an MK8s target are both enabled, cxcli ensures the target-scoped `external-secrets` app row by default so the ESO controller is present. `create` and `component add` materialize that dependency before their field wizard prompts, so operators can review the app row in the same pass that introduced MysteryBox. Native sync defaults on in that selected-backend wizard path: when it is enabled for an MK8s target, cxcli renders non-built-in workload namespaces, one `ClusterSecretStore`, and generated namespace-scoped full-sync `ExternalSecret` resources into a generated post-Flux manifest next to the target's Flux files, and does not render the credential Secret into Git-managed output. The external-secrets HelmRelease installs only the ESO controller and CRDs; local deploy/Flux apply submits the post-Flux manifest after that HelmRelease is Ready so Kubernetes can discover the CRDs before `ClusterSecretStore` and `ExternalSecret` resources are created. These cxcli-managed ESO objects are not source-config content: `config.yaml` keeps only `deploy.targets[].secrets.mysterybox.*`, and normalization strips stale cxcli-managed MysteryBox ESO `extraObjects` from the external-secrets app row while preserving operator-authored chart objects. Local deploy/Flux commands treat the configured Kubernetes Subject Credentials Secret as the persisted ESO auth location; when it is missing, invalid, or stale, cxcli ensures the dedicated Nebius service account `mysterybox-sa`, grants only `mysterybox.payload-viewer`, creates an authorized key through the Nebius API, and writes the private key only into that runtime Secret before applying Flux. That IAM-management step suppresses Terraform runtime service-account env vars so app-only `flux apply` uses the operator's Nebius auth context, including the Nebius CLI access-token fallback for federation profiles, instead of accidentally using the Terraform automation identity. ESO exchanges it for Nebius IAM access tokens when calling MysteryBox.
- The generated `ClusterSecretStore` defaults to `apiDomain: api.nebius.cloud:443` and does not render `caProvider`. ESO connects to the Nebius public API with the controller image's normal public CA trust bundle; cert-manager and trust-manager are only relevant for private CA, TLS-inspecting proxy, self-signed, or custom-domain designs.
- Before local deploy/Flux commands apply GitOps resources for a MysteryBox-enabled target,
  cxcli runs a temporary in-cluster curl pod from the credentials Secret namespace against
  the configured `api_domain`. The check proves cluster DNS, egress, hostname validation,
  public CA trust for the current endpoint certificate, and that the endpoint returns an
  HTTP response, without hardcoding a CA issuer. The validation suppresses the exact HTTP
  status line in terminal output and report details so an expected root-endpoint `404` does
  not look like an error.
- After local `deploy` applies Flux resources and post-Flux ESO resources for a configured native sync target, cxcli runs
  a required `mysterybox_eso_connectivity` validation and records it in
  `generated/inventory/deploy-report.md`. It checks `ClusterSecretStore Ready=True`, every
  configured `ExternalSecret Ready=True`, and ESO controller logs since the current validation
  started for Nebius/MysteryBox TLS, certificate, unauthorized, or permission errors. Optional
  validation skip flags do not disable this required guardrail.
- The operator identity running deploy/Flux must be allowed to manage service accounts, IAM groups, and access permits in the target project so cxcli can create and bind `mysterybox-sa`; that created account itself receives only `mysterybox.payload-viewer`.
- Rendered ESO native MysteryBox references are ID-oriented. Source config does not carry raw ExternalSecret specs; cxcli derives full-secret `ExternalSecret.spec.dataFrom[].extract` entries from declared `mysterybox.inputs.secrets`, configured `sync_namespaces`, and Terraform `secret_ids` output. A declared Secret `kubernetes_secret_name` controls the generated `ExternalSecret` name and target Kubernetes Secret name; omitted values default from the MysteryBox Secret name. A declared Secret `version_id: mbsecver-...` pins the full extraction to that MysteryBox version; empty or `n/a` version IDs omit the rendered `version` field and let ESO read the primary version.
- The generated sync path resolves each declared Secret name through Terraform `secret_ids` output to a Terraform-created `mbsec-...` ID. Source config does not expose raw ExternalSecret fields such as `secret_name` or `mysterybox_instance_id`; multiple MysteryBox component instances are resolved from the enabled `mysterybox` component rows, and externally managed MysteryBox Secrets are intentionally out of scope for this simplified generated sync model.
- The generated store defaults to cluster-wide access: `allow_all_namespaces: true` omits `ClusterSecretStore.conditions`. Restricted access is opt-in with `allow_all_namespaces: false`, which renders `ClusterSecretStore.conditions.namespaces` from the same non-empty `sync_namespaces` list that receives generated ExternalSecrets. In both modes, cxcli renders Namespace objects only for configured sync namespaces that are not built-in Kubernetes namespaces such as `default`; the `ExternalSecret` itself can still target `default`. The namespace condition controls which namespaces may reference the shared store, but the dedicated Nebius service account still defines the actual upstream read boundary, so namespace RBAC and the `mysterybox-sa` `mysterybox.payload-viewer` grant must be designed together.
- Existing VMs need a stop/start cycle after changing journald labels before the Monitoring agent picks up the new configuration.
- Public docs say omitted `deploy.observability.vm.logs.systemd_units` means all systemd services. cxcli keeps that default, but explicit units are still the deterministic smoke-test path.
- The standalone VM collector path is different from the built-in Monitoring-agent path operationally: it is installed/configured by cloud-init on first boot from the package source materialized out of `component_cli_settings.yaml` and currently assumes an attached service account plus a module-managed Ubuntu-family image.
- The detailed Kubernetes-agent docs define logs, metrics, and traces. That is the signal contract cxcli follows for MK8s, even though the public agents overview page summarizes the Kubernetes agent more narrowly.
- Grafana is the only read-side tool cxcli deploys automatically for MK8s observability. Prometheus configs, LogCLI environment variables, and any external Grafana instance remain operator-side concerns; the deploy report keeps the read endpoint URLs visible for those external tools. For bundled Grafana, the catalog still binds Metrics, Logs, and Traces dashboard signals for validation and runtime status, while the deploy report lists the cxcli-owned bundled dashboards directly. The bundled catalog pair binds Metrics to a cxcli-owned Kubernetes dashboard that uses `Nebius User Metrics`, current `query_result(...)` variables, cAdvisor/container metrics keyed by `kubernetes_io_hostname`, and standard CPU, memory, pod, container, and network panels; GPU to a cxcli-owned Kubernetes GPU dashboard that uses `Nebius Services`, `mk8s_cluster_id`, and DCGM metrics for only current GPU nodes in the selected cluster; Logs to a cxcli-owned Loki dashboard that queries the `default` bucket and Kubernetes labels such as `k8s_namespace_name` and `k8s_pod_name`; and Traces to a cxcli-owned Tempo dashboard that reads `Nebius Traces` and stays empty until workloads emit OTLP traces. The bundled catalog keeps one Nebius service dashboard import as an example under `Nebius Services`; cluster-scoped MK8s dashboards are cxcli-owned JSON so cxcli can control variable scoping and avoid stale label-index values. The report uses direct bundled-dashboard links, adding the target cluster variable when available, and intentionally omits separate Metrics/Logs/Traces shortcut rows to avoid duplicating that list. Operators can run `validate-dashboards <config.yaml>` after deploy to verify every catalog dashboard source against the live Grafana datasource/read-endpoint chain.
- cxcli references maintained upstream third-party artifacts from `component_sources.yaml` instead of vendoring them. The bundled observability console uses the maintained Grafana community Helm chart, leaves Grafana image registry/repository/tag on that chart's defaults so the chart version and chart `appVersion` stay the single source of truth, keeps a single Grafana.com service dashboard import as an example, ships cxcli-owned Kubernetes dashboard JSON package assets, and uses Envoy Gateway for Gateway API load-balancer exposure. The catalog-created EnvoyProxy sets the generated public LoadBalancer service to `externalTrafficPolicy: Cluster`, because Nebius Managed Kubernetes load balancers reject Envoy Gateway's default `Local` policy. CPU-only platform/observability charts use hard node affinity with `nebius.com/gpu NotIn ["true"]` so Grafana, Envoy Gateway, cert-manager, ExternalDNS, External Secrets, and n8n do not consume GPU worker capacity by default. The catalog defines this block once as YAML anchor `&nebius_cpu_only_node_affinity` and reuses it with aliases, but rendered HelmRelease values contain ordinary Kubernetes affinity objects rather than YAML anchor semantics. Because it is hard affinity, GPU-only clusters need an operator override or CPU node capacity for these platform pods. Third-party binaries, Helm charts, container images, package repositories, and Grafana.com dashboard imports referenced by the catalog remain governed by their own upstream licenses, support terms, usage terms, and distribution policies. This repository's license covers the cxcli source, bundled cxcli-owned dashboard JSON, and generated automation, not the operator's deployed use of referenced third-party artifacts.

### Onboarding Workflow

Use this sequence when onboarding observability for any bundled or new service:

1. Pick the supported control surface first.
   - `mk8s`: Nebius Observability Agent for Kubernetes plus optional app-side metric-target metadata.
   - `vm`: built-in Monitoring agent plus the supported Compute label contract for journald collection from systemd services.
   - `vm` standalone collector: only when the product requirement is explicit public write-side VM ingest and the built-in Monitoring-agent path is not enough.
   - If Nebius already provides a managed agent path, keep that path authoritative unless the user is explicitly asking for the standalone collector behavior.
2. Declare observability metadata in the right catalog.
   - Put component sources and dashboard JSON/Grafana.com dashboard source entries in `component_sources.yaml`.
   - Put global endpoint templates, default toggles, app metric targets, Grafana datasource and dashboard signal bindings, and source-specific guardrails under `component_cli_settings.yaml`.
3. Expose only the customer-facing project contract in `config.yaml`.
   - `deploy.targets[].observability.enabled`
   - `deploy.targets[].observability.kubernetes.*`
   - `deploy.observability.vm.logs.*`
   - `deploy.observability.vm.collector.*`
4. Materialize runtime state during normalization and render.
   - MK8s: chart rows plus managed `values.config.*`
   - VM: supported `nebius.o11y.systemd-logs-collection.*` labels
   - VM standalone collector: hidden module inputs that bootstrap the catalog-defined public collector package plus the catalog-defined Prometheus agent companion
5. Validate and report the runtime contract.
   - Fail fast on unsupported `deploy.targets[].observability.*` or `deploy.observability.*` keys or wrong types.
   - Generated reports must say which agent path is active, which signals are enabled, and which endpoints apply.
   - Standalone collector validation must keep the separation clear: service account required, at least one signal required, and built-in journald-label logging cannot be enabled at the same time as standalone collector journald logging.
6. Prove the live path.
   - MK8s: verify the Helm release, signal collection, and relevant read/write paths.
   - VM: verify labels, agent services, journald forwarding when enabled, and Monitoring readback for metrics.
   - VM standalone collector: verify the package/service bootstrap, Prometheus agent bootstrap, public write-side log/metric readback, and metadata-token auth behavior.

## Config Model

Runtime config root keys:

- `version`
- `client_info`
- `infra`
- `apps`
- `deploy`

Canonical `client_info` keys:

- `client_name`
- `nebius.tenant_id`
- `nebius.project_id`
- `nebius.region_id`
- `notifications.email_enabled`
- `notifications.email`
- `notifications.email_enabled` is the single per-client enable/disable switch for deploy-report email delivery across local runs and CI.
- In `create`, leaving the optional notifications email blank writes `notifications.email_enabled: false` and `notifications.email: null`.

Legacy `client_info.env` and `client_info.cluster_name` are not supported.

Canonical model is dynamic:

- `infra.components[]`: `id`, `instance_id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `instance_id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Source catalogs use `release.name`; project `config.yaml` uses `release-name`. Alias keys are intentionally unsupported.
- Static nested component blocks are not accepted.

Commands operate from this dynamic model with infra source metadata resolved from the active `component_sources.yaml`, not pinned in `config.yaml`. New starter configs omit `infra.components[].source` and `infra.components[].version`.

## Command Workflow

The command boundary is intentional:

- Generator-side commands operate on `config.yaml`.
- Project-level runtime commands (`deploy`, `destroy`, `email`) also start from `config.yaml` and resolve sibling `generated/`.
- Bundle-level runtime commands keep artifact-specific boundaries:
  `validate-generated` accepts any path under `generated/`, `terraform *`
  accepts `generated/` or `generated/infra/`, and `flux *` accepts
  `generated/` or `generated/flux/`.
- Customer CI is artifact-driven and should deploy only from canonical `<tenant-folder>/<project-folder>/generated/**` paths.
- `create` owns project identity and initial scaffold creation from a deployments root.
- When `create` targets an already-existing resolved project folder for the same `tenant_id`/`project_id`, interactive mode warns and asks for confirmation before recreating that folder from scratch; non-interactive mode requires `--force`.
- Interactive `create` prompts for `tenant_id` / `project_id` first and only warns when that resolved target already exists. Choosing a different new project under the same deployments root does not trigger an overwrite warning.
- Unless `--tenant-id` / `--project-id` were passed explicitly, interactive `create` starts those identity prompts blank instead of prefilling values from an existing project under the deployments root.
- After `create` writes the resulting `config.yaml`, it runs the internal warning-only post-create validation by default; `--no-validate-config` is the explicit escape hatch.
- `component add`/`component remove` are the day-2 config-editing commands for an already existing `config.yaml`.
- Live Helm chart defaults remain implicit in the chart and are not persisted into `config.yaml`; the wizard may surface them as prompt defaults, but only explicit chart overrides are written.
- CLI help should label positional targets explicitly as `DEPLOYMENTS_ROOT`, `CONFIG_YAML`,
  `GENERATED_PATH`, or `COMPONENT_SOURCES_YAML` so operators can tell the expected path type
  from the first `--help` screen.
  `auth` is the exception: it has no positional path and may also run `--validate-profile`
  across all cached profiles when no project/config target is provided.

### `create <deployments-root>`

- Creates one name-derived tenant/project folder and `config.yaml`.
- Operators still enter `tenant_id` / `project_id`; the CLI resolves tenant/project names only for the filesystem path after validation.
- Wizard-first for identity and component prompts (unless `--no-interactive`).
- Uses source-driven infra/app entries.
- Resolves app dependencies from live Helm chart metadata (`Chart.yaml`) when available.
- Resolves infra field options from live Nebius APIs where option sources are inferred.
- This is the bootstrap path because it owns project identity discovery/validation and initial directory creation.
- If the resolved project folder already exists for the same `tenant_id`/`project_id`, overwrite is explicit: interactive mode confirms, non-interactive mode requires `--force`, existing component selections are not merged, and only that resolved folder is recreated.
- A deployments root owns one cxcli-managed `.gitignore` block for every `<tenant-folder>/<project-folder>` beneath it. `create` rejects a target path nested under an ancestor cxcli-managed deployments root instead of creating a second managed `.gitignore`.

### `component list <config.yaml>`

- Read-only inspection of the current project component state against the active source catalog.
- Reports enabled component instances and reusable catalog component types, split between infra modules and app charts.

### `component add <config.yaml> [component-selector...]`

- Adds source-defined components to an existing project config without rerunning `create`.
- Component catalog entries are reusable types; each newly added infra row has its own `instance_id`; app chart cluster placement is expressed by setting the app row `instance_id` to the cluster target id.
- In non-interactive multi-target configs, target-bound app additions use `<app-id>@<target-id>` and fail fast when the target is omitted.
- Interactive mode prompts for infra first when component ids are omitted, can finish an infra-only add, and only asks for apps when no infra was selected or the operator explicitly chooses to add apps too.
- Interactive mode confirms the add before editing `config.yaml`.
- Auto-resolves app chart dependencies from chart metadata and app
  `release.install_after` prerequisites before persisting the updated selection.
- Runs the field wizard only for newly added components; existing component values remain untouched.
- The field wizard offers all discoverable required and optional fields for each newly added component, keeping module/chart defaults virtual unless the operator overrides them.
- Accepts simple string-list Terraform inputs as comma-separated prompt values and other complex inputs such as maps/objects/object-lists as single-line YAML/JSON prompt values so reusable modules do not need CLI-specific scalar shims.
- Validates the active source/settings catalog by default before editing `config.yaml`, matching `create`.
- Reuses the existing project tenant/project scope and validates it non-interactively before provider-backed prompts, instead of silently downgrading dynamic Nebius lookups.
- Non-interactive mode accepts one or more component selectors: `<component-id>`, `infra:<component-id>`, `apps:<component-id>`, `all`, `none`, or `<component-id>@<instance-id>`.
- Repeats of the same selector are no-ops when that exact row is already enabled. `<component-id>@<new-instance-id>` controls the new `config.yaml` `instance_id` when another infra or app-only row is intentional; for target-bound app charts, the suffix is the cluster target id and becomes `apps.charts[].instance_id`.
- Supports `--validate-sources` for a full catalog preflight.
- These commands update only `config.yaml`; existing `generated/` artifacts and
  live resources are unchanged until `render` refreshes the generated bundle and
  a deploy/destroy command is run. After the edit, the expected source-config
  loop is `validate`, then `render`.

### `component remove <config.yaml> [component-selector...]`

- Removes enabled component rows from an existing project config without rerunning `create`.
- Interactive mode prompts separately for infra and apps selections when component ids are omitted.
- Interactive mode confirms the removal before editing `config.yaml`.
- Non-interactive mode accepts enabled row selectors: `<component-id>`, `infra:<component-id>`, `apps:<component-id>`, `all`, `none`, `<instance-id>`, or `<component-id>@<instance-id>`.
- When more than one instance of the same component type is enabled, non-interactive remove must target an exact `instance_id` or `<component-id>@<instance-id>`.
- When removing a cluster target, also removes app chart rows and `deploy.targets[]` settings bound to that target.
- Fails fast when the resulting config would still break app dependencies or component input bindings.
- These commands update only `config.yaml`; existing `generated/` artifacts and
  live resources are unchanged until `render` refreshes the generated bundle and
  a deploy/destroy command is run. After the edit, the expected source-config
  loop is `validate`, then `render`.

### `validate-sources [component_sources.yaml]`

- Validates `component_sources.yaml`, sibling `component_cli_settings.yaml`, resolved Terraform module sources, and resolved Helm chart sources.
- Keeps the check fast: source resolution, catalog shape, child-module/chart layout, and CLI-facing surface validation only. It does not replace full `terraform validate` in example roots or `helm lint`.
- Accepts an optional positional `component_sources.yaml` path in addition to the global `--component-sources-file` override. The paired settings file is resolved as sibling `component_cli_settings.yaml`.

### `validate <config.yaml>`

- Runs the runtime validation stack: config/catalog load, active source checks,
  dependency checks, Terraform module input/schema checks, strict readiness,
  MK8s preflight, then a fail-fast live Nebius quota/capacity phase.
- Prints one concise validated-scope list after the phase run, with separate
  `infra` and `apps` sections and per-group entries such as `Compute`,
  `Storage`, `Platform`, or `Workloads`.
- Adds deployment-readiness checks:
  - placeholder rejection
  - chart source/dependency checks
  - module source and required-variable checks
  - provider-schema/resource checks when available
- Adds the same live Nebius quota/capacity assessment used by `quota-check`. GPU quota dimensions are resolved from the live Capacity Dashboard for the exact platform/preset/fabric shape, interpreted as VM slots for that preset, and converted to GPU units before comparison, while non-GPU dimensions still use the regular quota allowance APIs.
- Fails `validate` on confirmed live quota/capacity insufficiency, while unresolved live limits stay warning-only.
- For existing rendered/deployed MK8s bundles, uses the same best-effort sibling generated manifest plus Terraform state discount as `quota-check`, so an unchanged managed cluster does not fail validation as a fresh capacity request. If generated state cannot be read, the check falls back to the full desired source-config shape.
- Reuses the common runtime-validation result instead of rerunning the full common validation stack again before the readiness-only checks.

### `validate-dashboards <config.yaml>`

- Validates enabled bundled Grafana dashboard sources against the live Grafana
  instance for the project config. Signal-bound dashboard sources are validated
  for runtime status, while `deploy-report.md` lists the bundled dashboard set
  directly instead of separate Metrics, Logs, and Traces shortcuts. Other
  catalog dashboards are checked as dashboard sources too.
- Checks the concrete chain from `observability.endpoints.read.<key>` to
  Grafana datasource UID/type to dashboard JSON query contract.
- For target-scoped Grafana rows, resolves an explicit kube context from
  generated Grafana status, the deploy report, the matching current local
  kubeconfig context, an unambiguous local kubeconfig context, or the generated
  MK8s handoff before any `kubectl` call. If a target context cannot be
  resolved, validation fails fast instead of using an unrelated ambient current
  context.
- Uses Grafana datasource proxy APIs so validation exercises the same
  Prometheus, Loki, and Tempo read endpoints that Grafana panels use.
- Prometheus checks metric names, required label keys, and representative
  PromQL queries. For target-scoped dashboard sources it resolves the target
  MK8s cluster ID from generated Grafana status, generated inventory, or the
  persisted kube context and validates cluster-filtered selectors such as
  `k8s.cluster.id` instead of letting another cluster's data satisfy the check.
  Loki checks bucket-aware label discovery and representative LogQL queries in
  the same target-aware way. Tempo checks TraceQL search reachability and warns
  when the endpoint is reachable but has no traces in the selected window.
- Prints each dashboard result with `Source:`, optional `Checks:`, grouped
  `Warnings:`, and grouped `Errors:`. Grafana.com imports are source provenance,
  not warnings; Prometheus dashboard sources show whether metric and label
  names matched the selected datasource.
- Shows a timed dashboard-level spinner/progress display while it waits on live
  Grafana datasource/dashboard API calls. The total is every target-bound
  Grafana.com and cxcli-owned dashboard binding, and the active item is labeled
  as `<target-id>: <folder>/<dashboard>`.
- Supports `--target <instance-id>` for multi-target configs.

### `quota-check <config.yaml>`

- Runs the same live Nebius quota/capacity assessment used by `create`, `render`, and `deploy`, but as an explicit read-only operator command against one project config. It always queries current Nebius state and does not reuse a cached create-time result.
- For existing rendered/deployed MK8s bundles, best-effort state adjustment reads the sibling generated manifest plus Terraform state and discounts quota already managed by that bundle. That keeps manual day-2 edits such as changing a node count from 4 to 6 focused on the net-new 2 nodes when state is available, while still falling back to the full desired source-config shape when no generated state can be read.
- Quota assessment prefers operator auth such as an IAM token or Nebius CLI profile when available, then falls back to runtime project auth. That keeps tenant-scope quota and Capacity Dashboard reads working during normal operator reruns even after cxcli has bootstrapped a project-scoped runtime service account into the process environment.
- GPU quota dimensions are centralized on the live Capacity Dashboard `resource-advice` surface for the exact platform + region + preset + fabric shape. cxcli treats the returned availability as VM slots for that preset, multiplies by the selected preset's GPU count, and compares the result with `compute.instance.gpu.*`; a two-node `8gpu-*` request requires 16 GPUs and passes when at least two matching VM slots are available. cxcli no longer overlays a separate Capacity Block Group-specific GPU path or a synthetic `compute.gpucluster.count` check.
- Prints a concise per-component confirmed summary for the quota dimensions that were successfully checked, including the exact checked quota names listed one per line. Components with coverage gaps still appear there with a partial-coverage note, while confirmed shortages and unresolved live limits stay out of that list.
- Returns success when no confirmed insufficiency is found, even if some live quota dimensions remain unresolved; those unresolved limits and coverage gaps are still printed as warnings.
- Coverage-gap warnings are rendered as one component line with a vertical `gaps:` list underneath so each unresolved reason appears on its own line.
- Optional `--all-regions` prints per-region availability for the same shape across all discovered quota regions plus any GPU regions returned by the Capacity Dashboard, but it does not change pass/fail semantics.
- When quota-check ends with confirmed insufficiency and `--all-regions` was not requested, the CLI prints either the direct `quota-request` remediation command for requestable tenant/project quota shortages or a GPU Capacity Dashboard shape-change hint for capacity-only shortages, plus the exact `quota-check --all-regions` rerun command as a diagnostic next step.
- Coverage-gap-only warnings remain non-fatal and indicate partial estimator coverage, not a confirmed shortage in the quota dimensions that were successfully checked. The unresolved reasons are listed one per line under the affected component.
- Returns a non-zero exit status only when the enabled infra shape is confirmed to exceed currently available live quota.

### `quota-request <config.yaml>`

- Reuses the same live quota assessment as `quota-check`, but keeps the object
  model explicit: `QuotaAllowance` confirms what quota currently exists, while
  `QuotaRequest` is the separate resource used to ask for a limit change.
- Acts only on shortages confirmed by the current live assessment. If the
  config is currently sufficient, it exits as a no-op rather than pre-requesting
  quota because the config exists.
- Supports day-2 manual `config.yaml` edits by reusing the same best-effort
  MK8s state discount as `quota-check`; the planned `QuotaRequest` target is
  based on the confirmed net-new shortfall when existing generated state is
  available.
- Capacity Dashboard-only GPU availability shortages are not quota-request
  targets. Operators must choose another platform/preset/fabric or region with
  available capacity, or wait for capacity to appear.
- No verified public Nebius quota-request API surface is assumed here.
  Automatic submission is an internal-only path: it works only on the Nebius
  internal network for Nebius employees/operators when that internal request
  path is available. Otherwise the command falls back to a manual console step.
- Requests only the constraining tenant/project scopes; unresolved live limits
  and estimator coverage gaps remain report-only and are not auto-requested.
- When internal auto-submit is available, cxcli asks the internal
  quota-recommendation service for the final request set before submission, so
  related quotas can move together instead of blindly mirroring the raw
  insufficiency list.
- When internal auto-submit is unavailable or denied, the command prints the
  exact tenant/project quota entries that still need follow-up under
  Administration -> Limits -> Quotas.
- That manual fallback also prints the minimum total target limit and minimum
  increase to request for each confirmed shortage, so the console path keeps
  the same actionable numbers as the auto-submit path.
- When the report contains coverage gaps only, `quota-request` still prints the
  per-component unresolved reasons before the final no-op summary so operators
  can see why nothing was submitted.
- For bundled MK8s node-group disk-size quota, exact `compute.disk.size.*`
  requests work whenever cxcli can resolve the node-group preset resources plus
  disk type and therefore materialize the effective boot-disk size/type, or
  when the equivalent first-class boot-disk fields / `template.boot_disk`
  override values are already explicit in `config.yaml`. If neither path
  resolves the exact shape, the CLI keeps the result as a coverage gap instead
  of issuing a blind quota request.
- Submission is best described as a request, not an immediate guarantee of
  granted quota. Current quota allowances remain unchanged until the request is
  approved, and operators should submit or track follow-up status in the
  Nebius web console under Administration → Limits → Quotas.

### `render <config.yaml>`

- Writes deterministic artifacts under `generated/infra`, `generated/flux`, and `generated/inventory`.
- Requires the project `config.yaml` path explicitly; passing `generated/` is a usage error and should be rejected with targeted guidance instead of a raw filesystem exception.
- Runs pre-render runtime validation before any render side effects: load config/catalog, validate active component sources, validate dependencies, then validate Terraform module inputs/schema.
- Writes `generated/nebius-cxcli-manifest.json`, which snapshots the runtime config and deployment metadata needed to operate on the generated bundle later.
- Runs a best-effort live Nebius quota assessment for the rendered infra shape, discounts capacity already managed in the current sibling generated Terraform state when available, persists that report in the generated manifest, and warns instead of blocking when quota is insufficient or only partially known.
- Keeps non-blocking coverage-gap detail in the persisted quota report, while routine `render` terminal output focuses on confirmed shortages and live lookup failures. The explicit `quota-check` command remains the verbose terminal surface for coverage gaps.
- Warns before overwriting an existing generated bundle, because rerendering is the replace path back to the original `config.yaml` contract.
- The overwrite warning should not trigger on the scaffold created by `create` alone; empty generated subdirectories and the placeholder `generated/inventory/deploy-report.md` are not treated as meaningful existing rendered artifacts.
- Renders into a hidden sibling staging directory first and swaps it into `generated/` only after the replacement bundle is complete, so a failed rerender leaves the current bundle intact.
- When Terraform is available from `PATH` or the managed download path, attempts backend-disabled `terraform init -backend=false` to produce/update `.terraform.lock.hcl`.
- Removes transient `.terraform/` workdir state after lockfile generation so the canonical rendered bundle stays clean.

### `validate-generated <generated-path>`

- Validates an existing generated artifact bundle without rerendering it, including generated-bundle readiness and live quota/capacity.
- After backend init, the generated-bundle quota/capacity phase is state-aware for bundled MK8s reruns: cxcli reads the current Terraform state, reconstructs the MK8s quota already managed by that bundle, and subtracts that managed baseline before comparing the desired bundle against live Nebius quota/capacity.
- That keeps unchanged existing-cluster reruns idempotent instead of treating them like fresh creates, while still failing when the rerun would add real net-new capacity such as more nodes or a larger GPU shape.
- Confirmed generated-bundle quota/capacity failures print the exact source-config follow-up commands for `quota-request` and `quota-check --all-regions`.
- Runs Terraform validation against `generated/infra`.
- Runs `kubectl kustomize` against each rendered Flux tree when apps are enabled.
- For bundled MK8s, the generated-bundle Terraform-validation pass also checks
  live Nebius cluster / derived GPU-cluster names against the current
  Terraform state and fails fast when a stale unmanaged live resource would
  make `terraform apply` hit `AlreadyExists`.
- Optional `--portable` enforcement rejects generated bundles whose Terraform root still embeds local filesystem module sources.
- Reports visible validation phases for strict readiness, MK8s preflight, backend auth preparation, live quota/capacity, Terraform validation, Flux manifest validation, and optional portability enforcement.

### `deploy <config.yaml>`

- Deploys an existing generated bundle as a reconcile/apply path: terraform apply, deploy-report refresh, then local Flux apply. On success, the terminal footer includes the complete report path at `generated/inventory/deploy-report.md`.
- Requires `config.yaml` explicitly and resolves the sibling `generated/` directory, while still deploying from the generated manifest so source-file edits after render do not silently alter the applied bundle.
- The source chain is explicit: changes to `config.yaml` affect deployment only after `render` updates `generated/nebius-cxcli-manifest.json`; `deploy` then recreates `generated/infra/terraform.auto.tfvars.json` from that manifest before Terraform runs.
- Before Terraform apply, runs a generated-bundle deploy preflight: strict readiness checks against the manifest runtime config, MK8s network preflight, live Nebius quota/capacity validation, Terraform validation for `generated/infra`, and `kubectl kustomize` against each rendered Flux tree when apps are enabled. On bundled MK8s, that Terraform-validation pass now also fails fast on live MK8s cluster / derived GPU-cluster name collisions that are not already tracked in the current Terraform state, while treating Nebius `NOT_FOUND` responses as the expected "resource is absent" case.
- Ensures remote-state backend bucket exists before Terraform init/apply.
- The generated-bundle quota/capacity preflight uses the same state-aware MK8s baseline subtraction as `validate-generated`, so a sequential rerun of the same managed cluster does not fail quota as if all of its existing nodes still needed to be created from scratch.
- MK8s status polling still fails fast on fresh terminal node-group API errors from the current run, but it ignores stale old node-group error events that predate the current watcher start so Terraform replacement of a previously failed group can begin.
- Does not rerender from `config.yaml`.
- Uses `generated/nebius-cxcli-manifest.json` to recover the runtime config snapshot and deployment metadata.
- The live quota/capacity preflight still fails fast with a quota-increase message and the exact `quota-request` / `quota-check --all-regions` follow-up commands when the rerun would add net-new capacity that exceeds currently available quota.
- Applies deploy-time validations from the generated manifest. GPU checks come from the target-facing `deploy.targets[].validations.mk8s_gpu.*` contract, while observability-enabled MK8s targets get `mk8s_observability_ingestion` when the active settings catalog leaves `primary_agent.validation` enabled. That guardrail verifies the live Nebius Observability Agent HelmRelease, signal config, DaemonSet readiness, and trace OTLP service EndpointSlice readiness. Native ESO MysteryBox sync targets get a required `mysterybox_eso_connectivity` validation that checks in-cluster Nebius API TLS, `ClusterSecretStore Ready=True`, every configured `ExternalSecret Ready=True`, and ESO controller log errors since the current validation started. The generated Markdown report keeps platform/security selections visible in its component summary, including MysteryBox, External Secrets Operator, NVIDIA GPU Operator, and NVIDIA Network Operator when those components are enabled. Local `deploy` can bypass optional validations with `--skip-validations` or a subset with repeatable `--skip-validation <kind>` flags such as `nccl`, `gpu-visibility`, or `observability-ingestion`; required validations still run and those one-run overrides do not rewrite the source config.
- Keeps non-blocking coverage-gap detail in the generated manifest instead of repeating it in normal `deploy` terminal output. Operators can run `quota-check` against the source config when they need the full coverage-gap summary in the terminal.
- Uses `deploy.status_watchers[]` from the generated manifest to decide which Nebius SDK pollers to run for infra status reporting. Those watcher specs are derived from `components.infra.<id>.status` in the active catalog at render time.
- Each watcher spec resolves `parent_id` and `resource_name` from the enabled component's `inputs` payload in `config.yaml`, following the catalog-declared `status.parent_input` and `status.name_input` paths. `status.name_input` may resolve a scalar resource name or a collection of named objects, in which case the CLI expands one component row into multiple watcher specs.
- Service-specific pollers must read the Nebius SDK response shape for that API directly, rather than assuming a generic `items[]` field, so in-progress resources remain visible during long-running applies.
- Fail-fast error detection is also service-specific: MK8S uses node-group event logs, while MSP PostgreSQL, SFS, object-storage buckets, compute instances, and MysteryBox secrets use live resource state plus the latest terminal Nebius operation status for that resource.
- Bundled jump-host Terraform modules now declare `status.kind: nebius.compute.instance`, so their long-running instance creates participate in the same SDK-backed status reporting and terminal-failure abort path as the other bundled infra modules.
- The bundled `mysterybox` module now declares `status.kind: nebius.mysterybox.secret` with `status.name_input: secrets`, so each configured secret participates in the same catalog-driven status reporting and abort path.
- When an older generated manifest does not contain watcher metadata yet, `deploy` may rebuild watcher specs from the loaded runtime config plus the active local catalog as a fallback.
- Must stay idempotent for the same generated bundle, but should not change into a create-only mode that ignores drift or desired updates to already managed resources.
- Operators who need a non-mutating preview should use `terraform plan` against the same generated bundle before `deploy`.

### `destroy <config.yaml>`

- Destroys all rendered project resources represented by the existing generated bundle as the destructive inverse of `deploy`: `destroy` requires `config.yaml`, resolves sibling `generated/`, and then uses the generated manifest as the authoritative project-wide teardown contract. When enabled apps target an external or current cluster, delete the rendered Flux resources first and then run Terraform destroy against the rendered infra bundle. When the generated bundle destroys the handed-off cluster directly, skip the separate Flux delete step and rely on cluster teardown instead.
- Does not rerender from `config.yaml`.
- Uses `generated/nebius-cxcli-manifest.json` to recover the runtime config snapshot and deployment metadata.
- Uses the same generated manifest watcher specs/runtime auth/backends as the apply path.
- Rendered app teardown is best-effort. If deleting the rendered Flux resources fails, the CLI warns and still continues with Terraform destroy because the rendered infra bundle remains the authoritative teardown path.
- Requires explicit confirmation in interactive mode and `--yes` in non-interactive mode.
- Does not uninstall Flux controllers or mutate GitHub workflow/bootstrap state.

`object-storage` is modeled as one bucket per enabled component instance. That keeps `config.yaml`, the field wizard, and the Terraform module contract aligned on scalar inputs like `inputs.name`, `inputs.versioning_policy`, and `inputs.protect_from_destroy` while still allowing multiple buckets in one project through distinct `instance_id` values.

Modules that expose collection/object inputs, such as `mysterybox.secrets`, `ssh-jumphost.allowed_cidrs`, `wireguard-jumphost.clients`, or MK8s override objects, should keep those Terraform-native shapes. For MysteryBox, that means `inputs.secrets` is a list of secret objects where `name` is the stable identity, each secret carries a non-empty `payload` mapping with named `text` or `file` payload entries, and `version_id` records the current primary MysteryBox version ID. Before first deploy `version_id` is empty or `n/a`; after Terraform creates the initial primary version, cxcli updates it from the module output. Later rotations are operator-owned in MysteryBox and are reflected by replacing `version_id` with the new `mbsecver-...` primary ID. The optional `kubernetes_secret_name` field is cxcli-only sync metadata: render strips it before passing `secrets` to Terraform and uses it only for generated ESO target Secret naming. The CLI prompts this one product-specific object through a guided Secret/key loop while still writing the Terraform-native list/map shape; simple `list(string)` prompts use comma-separated input, and other complex module inputs use the generic single-line YAML/JSON prompt. The corresponding `payload_values` remain outside config and generated files; cxcli renders only the root variable pass-through and expects first-deploy runtime `TF_VAR_*` injection keyed by secret name and payload key.

### `bootstrap-ci <config.yaml>`

- Generates `.github/workflows/nebius-deployments.yml`.
- Re-running it automatically reconciles that CLI-managed workflow file to the latest template for the target repo/deployments path.
- Generated customer workflow is artifact-driven: it watches and deploys only canonical `<tenant-folder>/<project-folder>/generated/**` paths.
- Generated customer workflow also supports manual `workflow_dispatch`, which runs discovery in `--all` mode for the configured deployments scope.
- `config.yaml` remains in the customer repo as a manual render/replace contract and does not trigger customer CI deployment.
- The target `config.yaml` must already live inside the customer git repository because the workflow is written at that repo root.
- The command resolves the target GitHub repo from the checkout `origin` remote. `--github-repo` is only an explicit override for missing, non-GitHub, or remapped remotes.
- `--github-token-env` controls the GitHub API token used for workflow/environment reconciliation, SMTP sync, and optional Nebius auth bootstrap.
- Every run reconciles local SMTP settings from `nebius-cxcli email --setup` into the matching GitHub Environment, including removal of stale GitHub SMTP settings when local SMTP is disabled.
- `--auth-bootstrap` controls only Nebius CI auth bootstrap/rotation. Disabling it does not disable workflow reconcile or SMTP reconcile.
- Because SMTP reconciliation happens on every run, `bootstrap-ci` still requires GitHub API access even when `--no-auth-bootstrap` is used.
- When `--cli-ref` is omitted, the generated workflow defaults to `main` for development builds and `nebius-cxcli-v<version>` for stable tagged releases.
- `--cli-ref` is the explicit escape hatch when generator-side automation must pin the generated customer workflow to a specific nebius-cxcli branch, tag, or commit for PR validation.
- `--cli-ref` selects the `nebius-cxcli` source ref to install from `nebius-ps-services`; it does not select or mutate the branch of the customer target repo.
- Example: `nebius-cxcli bootstrap-ci /path/to/config.yaml --cli-ref <branch|tag|sha>`.
- Generated workflows also support a GitHub repo/org variable override `NEBIUS_CXCLI_REF`, which takes precedence over the generated default ref.
- The intended ref controls are generator-time pinning via `--cli-ref` and optional runtime override via the GitHub variable; editing the generated workflow YAML is not required for normal use.
- Optional CI auth/environment-secret bootstrap creates the GitHub Environment and syncs Environment Secrets, but does not manage GitHub repo/org variables.
- Generated workflows always run the deploy-report email step after apply. `client_info.notifications.email_enabled` is the single send/no-send switch; when enabled but SMTP is not configured, the step warns and continues.

### `auth` (flag-driven)

- `auth --create` creates runtime auth cache/profile only when missing.
- `auth --recreate` always rotates runtime auth material and rewrites cache.
- `auth --validate-profile` inspects cached runtime auth profile metadata/private key and verifies Nebius auth public key visibility.
- Customer-side commands that run with `--auto-auth-bootstrap` also self-heal a stale cached
  runtime auth profile when the cached Nebius auth public key has been deleted or the cached
  private-key metadata is broken; healthy cached profiles are reused without rotation.
- `auth --bootstrap-ci` syncs local runtime auth cache material into GitHub environment secrets.
- `auth --profile` and `auth --sdk-config-file` target Nebius SDK config resolution; they do not require the standalone `nebius` CLI binary.

## Generator-side Commands

- `validate-sources`
  - Validates the active source/settings catalog contract and backing Terraform/Helm sources.
- `validate <config.yaml>`
  - Validates the project config contract and deployment-readiness gate before rendering.
  - Defaults to source profile `portable`; `--source-profile local` is available for local checked-out module workflows.
- `render <config.yaml>`
  - Produces the canonical generated Terraform/Flux/inventory bundle.
  - Recreates the managed `generated/` bundle from a clean layout without stale files and removes any legacy `generated/flux/flux-system` subtree.
  - Stages the replacement bundle under a hidden sibling directory and swaps it into `generated/` only after the staged bundle is complete.
  - Defaults to source profile `portable`; `--source-profile local` is explicit and produces non-portable generated Terraform sources for local testing.
  - If the target `generated/` bundle already exists, rerender is treated as a replace action:
    - interactive terminal: prompt before overwrite
    - non-interactive context: require `--force`

## Customer-side Commands

- `validate-generated <generated-path>`
  - Validates an already-rendered bundle without rerendering it.
  - CI and publish workflows should call `validate-generated --portable` before plan/apply. That command now reuses the same generated-bundle strict readiness, MK8s preflight, and live quota/capacity gate as `deploy` preflight.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation for Terraform validation (default enabled).
- `deploy <config.yaml>`
  - Full local deployment from the generated bundle: Terraform first, then deploy-report refresh for infra and apps artifacts, then Flux direct apply.
  - The command resolves sibling `generated/`, but the generated manifest remains the canonical deploy input.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Does not run `flux bootstrap`; GitOps bootstrap/reconcile stays explicit through `flux bootstrap` or the generated CI apply workflow.
  - Does not run `bootstrap-ci` automatically, even when the generated bundle is inside a git repository; GitHub workflow/environment bootstrap stays an explicit generator-side action.
- `destroy <config.yaml>`
  - Project-wide destructive teardown from the generated bundle: `destroy` resolves sibling `generated/`, then removes all rendered resources represented by the generated manifest. Rendered apps are deleted first only when they target an external or current cluster; otherwise Terraform destroy removes the handed-off cluster directly.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Uses guarded destroy recovery: stale-lock auto-unlock/retry first, then targeted MK8s stuck node-group cleanup only when live API state still blocks destroy.
  - Requires explicit confirmation or `--yes`.
- `terraform apply <generated-path>`
  - Infra-only apply from the generated Terraform bundle.
  - Accepts the project `generated/` directory or a path under `generated/infra/`; other generated subtrees are rejected.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `terraform destroy <generated-path>`
  - Infra-only destroy from the generated Terraform bundle.
  - Accepts the project `generated/` directory or a path under `generated/infra/`; other generated subtrees are rejected.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Uses the same guarded stale-lock and MK8s stuck-create recovery path as top-level `destroy`.
  - Requires explicit confirmation or `--yes`.
- `flux apply <generated-path>`
  - Apps-only direct apply from the generated Flux bundle.
  - Accepts the project `generated/` directory or a path under `generated/flux/`; other generated subtrees are rejected.
  - When the rendered manifest needs Terraform-backed handoff or app-input outputs, it initializes `generated/infra` first and reads the current outputs from state, but it does not run `terraform apply`.
  - Its pre-apply Flux API discovery is resource-type based, so it does not wait on app target namespaces that are expected to be created by the rendered manifests themselves.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `flux destroy <generated-path>`
  - Apps-only direct delete from the generated Flux bundle.
  - Accepts the project `generated/` directory or a path under `generated/flux/`; other generated subtrees are rejected.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - If the target cluster is reachable but Flux CRDs are already absent, the CLI prints a skip note instead of surfacing raw `kubectl` resource-mapping errors.
  - Requires explicit confirmation or `--yes`.
- `flux bootstrap <generated-path>`
  - GitOps bootstrap/reconcile path from the generated Flux bundle.
  - Accepts the project `generated/` directory or a path under `generated/flux/`; other generated subtrees are rejected.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default disabled).

## Supporting Commands

- `component list <config.yaml>`
  - Shows enabled and available catalog components for the current project.
- `component add <config.yaml>`
  - Day-2 config mutation path for adding source-defined components to an existing project.
- `component remove <config.yaml>`
  - Day-2 config mutation path for safely removing enabled components from an existing project.
- `create <deployments-root>`
  - Scaffolds one name-derived tenant/project folder with `config.yaml` and the generated skeleton.
  - Operators still enter `tenant_id` / `project_id`; the CLI resolves names only for the folder path after ID validation succeeds.
  - Interactive mode prompts for `tenant_id` / `project_id` first and only warns when that resolved target already exists; choosing a different new project under the same deployments root does not trigger an overwrite warning.
  - Unless `--tenant-id` / `--project-id` were passed explicitly, interactive mode starts those identity prompts blank instead of prefilling values from an existing project under the deployments root.
  - Keeps one root-level cxcli-managed `.gitignore` for all tenant/project folders and fails fast when the supplied root is nested below another cxcli-managed deployments root; nested root compatibility is not supported.
  - Runs internal warning-only post-create validation on the resulting `config.yaml` by default.
  - Runs a best-effort live Nebius quota assessment for bundled infra components and warns when the selected shape already exceeds current quota, but it does not block render or further config edits, does not reserve capacity, and is not a wizard-selectable deploy gate. Confirmed requestable quota shortages print the exact `quota-request <config.yaml>` follow-up command, while capacity-only GPU shortages point to choosing another available shape or region.
  - In the bundled MK8s flow, `gpu_nodes_preset` is chosen first from live SDK shape metadata, and `infiniband_fabric` is only offered afterward when that exact preset supports GPU clustering. Single-GPU presets are labeled as Ethernet-only testing/dev shapes rather than production distributed-training shapes. When tenant/project/region context is available, those GPU preset/fabric prompts also query the live Nebius Capacity Dashboard `resource-advice` surface, use those live rows as the source of truth for offered fabric names, annotate current on-demand/reserved availability for the exact selected platform/region/preset, and highlight the recommended default while still allowing the optional fabric field to stay unset. When any matching fabric has reserved VM slots, that reserved-capacity fabric is recommended ahead of on-demand-only fabrics because the reservation is bound to the fabric. Fabric-scoped capacity rows for single-GPU shapes are used only for ranking availability, not for exposing a fabric selector. Invalid stale fabric values still fail fast during validation instead of surviving until Terraform apply, and cluster-capable shapes with no live fabric rows fall back to manual entry instead of a baked-in static list.
  - If an operator leaves `deploy.targets[].validations.mk8s_gpu.nccl.enabled=true` on a non-cluster/Ethernet-only MK8s GPU shape, cxcli warns that the benchmark will run in Socket/TCPIP mode instead of InfiniBand / GPUDirect-RDMA. For 1-GPU presets the warning is explicit that the result is degraded and not representative of a production distributed-training environment, but the NCCL run still happens so operators can compare the measured bandwidth with RDMA-capable shapes.
  - Keeps non-blocking coverage-gap detail for `quota-check` and the persisted generated manifest instead of repeating it during normal `create` terminal output.
- `quota-check <config.yaml>`
  - Read-only live quota assessment for the enabled infra components in the current project config.
  - Reruns against current Nebius state every time instead of relying on the create-time warning result.
  - For existing rendered/deployed MK8s bundles, discounts capacity already managed in Terraform state when the sibling generated bundle is available, so day-2 scale edits are evaluated as net-new capacity.
  - Uses the same SDK-backed logic and component estimators as the render/deploy guard rails.
  - Prints a concise per-component confirmed summary for the quota dimensions that were successfully checked, including the exact checked quota names listed one per line. Components with coverage gaps still appear there with a partial-coverage note, while confirmed shortages and unresolved live limits stay out of that list.
  - Optional `--all-regions` replays the current config's quota requirements across all discovered tenant/project regions and prints per-region availability for the same shape. This remains quota-only, does not change pass/fail semantics, and does not revalidate platform/preset compatibility in those other regions.
- `quota-request <config.yaml>`
  - Plans requests only for confirmed live quota shortages in the current project config.
  - Exits as a no-op when the current live assessment has no confirmed insufficiency.
  - For manual day-2 MK8s scale edits, plans request targets from the net-new shortfall when generated Terraform state can be read.
  - Does not request pure GPU Capacity Dashboard capacity shortages that have no constraining tenant/project quota target.
  - Keeps live `QuotaAllowance` reads separate from `QuotaRequest` submission, so unresolved live limits and estimator coverage gaps remain report-only instead of becoming blind quota requests.
  - Uses the internal Nebius request path only when that path is available and permitted; otherwise it prints exact manual web-console follow-up targets with minimum total limits and increases.
- `bootstrap-ci <config.yaml>`
  - Generates or reconciles the customer workflow. The generated workflow watches and deploys only canonical `<tenant-folder>/<project-folder>/generated/**` paths.
  - When the deployments root is the repository root, the generated workflow uses `NEBIUS_DISCOVER_TARGET: .` and `*/*/generated/**` rather than a `./` path-filter segment.
  - Uses the same deployments-root `.gitignore` guard as `create` and `render`: if the inferred config root is nested under another cxcli-managed deployments root, the command fails before reconciling workflow files.
- `discover <deployment-scope-dir>`
  - Returns deployment-project discovery payload for CI.
  - Uses local git/filesystem discovery over readable project `config.yaml` files and does not call Nebius APIs.
  - Accepts the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - Scope filtering remains project-aware for both `--all` and changed-only mode, so a scoped `generated/` directory still maps back to that project `config.yaml`.
- `terraform plan <generated-path>`
  - Infra-only plan from the generated Terraform bundle.
  - Accepts the project `generated/` directory or a path under `generated/infra/`; other generated subtrees are rejected.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `terraform unlock <generated-path>`
  - Clears a stale remote Terraform state lock for a generated infra bundle.
  - Accepts the project `generated/` directory or a path under `generated/infra/`; other generated subtrees are rejected.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - `--force` overrides local safety checks and force-unlocks even when the lock owner is different or local processes are still active.
- `flux apply <generated-path>`
  - Applies rendered app resources from the generated Flux bundle and supports `--target <instance-id>` / `--all-targets` for multi-target MK8s bundles.
- `flux destroy <generated-path>`
  - Deletes rendered app resources from the generated Flux bundle, requires confirmation or `--yes`, and supports `--target <instance-id>` / `--all-targets` for multi-target MK8s bundles.
- `flux bootstrap <generated-path>`
  - Bootstraps or reconciles GitOps from the generated Flux bundle and supports `--target <instance-id>` / `--all-targets` for multi-target MK8s bundles.
- `email [config.yaml]`
  - Sends `deploy-report.md` via SMTP and fails if the rendered markdown file is missing.
  - Omits the positional path only when `--setup` is used.
  - Resolves sibling `generated/` automatically and still reads the runtime snapshot from the generated manifest instead of live source edits.
  - Reads the recipient from `client_info.notifications.email` in the generated-bundle runtime config snapshot, not from any inventory artifact.
  - SMTP is opt-in. Local operators enable it with `nebius-cxcli email --setup`, which writes `~/.config/nebius-cxcli/email.yaml` with host/port/STARTTLS/from and optional username/password.
  - Per-client delivery is controlled by `client_info.notifications.email_enabled` in `config.yaml`.
  - If email is enabled but SMTP is not configured, the command warns and exits successfully instead of failing the deploy/email flow.
  - Runtime `SMTP_*` environment variables override the local email config when present.
  - Masks tenant/project identifiers in the email subject/body while leaving the local `deploy-report.md` artifact unchanged on disk.
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.
  - Targets either `--project-config <config.yaml>` or `--project-id`; `--client-name` belongs only to the manual `--project-id` path.

## Idempotency Rules

- `create`: create-if-missing for a new resolved project folder; existing resolved targets for the same `tenant_id`/`project_id` require explicit overwrite confirmation instead of reconcile.
- `create --force`: deterministic overwrite for the same resolved project folder. It recreates only that folder and does not delete the deployments root or unrelated project folders.
- `component list`: read-only; safe to repeat.
- `component add`: idempotent for already-enabled exact selectors; repeating the same selector is skipped. Adding another infra or app-only instance requires an explicit new selector such as `mk8s@training-cluster`. Target-bound app charts are unique per chart id and cluster target, so duplicate `<chart-id>@<target-id>` adds are skipped instead of inventing a second target-bound row.
- `component remove`: idempotent for already-absent components; cluster-target removal also removes app rows and deploy-target settings bound to that target, while removals that would still violate dependency contracts are blocked.
- `validate-sources`: read-only; safe to repeat.
- `validate`/`quota-check`/`render`: deterministic and repeatable, aside from expected live provider/quota state changes.
- `render --force`: same rendered output for the same config, but intentionally bypasses the interactive overwrite confirmation.
- `validate-generated`: deterministic for a given generated bundle.
- `discover`: read-only; safe to repeat.
- `deploy`: convergent behavior expected from apply/reconcile against a fixed generated bundle.
- `destroy`: sequentially convergent for a fixed generated bundle, but intentionally destructive and confirmation-gated.
- `terraform plan`: read-only; safe to repeat.
- `terraform apply`: sequentially convergent for a fixed generated bundle; Terraform backend locking intentionally prevents concurrent mutation against the same state.
- `terraform destroy`: sequentially convergent for a fixed generated bundle, but intentionally destructive and confirmation-gated.
- `terraform unlock`: operationally idempotent; once a stale lock is cleared, reruns report that no lock is present.
- `flux apply`: sequentially convergent for a fixed generated bundle.
- `flux destroy`: sequentially convergent for a fixed generated bundle, but intentionally destructive and confirmation-gated.
- `flux bootstrap`: bootstrap once, reconcile on rerun when the cluster is already GitOps-bootstrapped.
- `email --setup`: local SMTP-config reconcile; repeating the same answers leaves the same config on disk.
- `email`: intentionally not idempotent because each successful run sends another message.
- `bootstrap-ci`: idempotent reconcile; reruns auto-update the CLI-managed customer workflow and re-check GitHub environment secret presence.
- `auth --create`: idempotent create-if-missing.
- `auth --recreate`: explicit rotation path.
- `auth --validate-profile`: read-only profile validation; safe to re-run.
- `auth --bootstrap-ci`: idempotent environment-secret upsert from local cache.
- `deploy` and other customer-side generated-bundle commands do not mutate GitHub CI state as a side effect.

## Validation Model

Validation layers:

1. Structural/runtime config checks (`runtime_validation.py`).
2. Dynamic payload shape checks (`validate_dynamic_payload_structure`).
3. Strict checks in CLI for deployment readiness.
4. Optional plugin validation via `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS`.

Bundled runtime validation selection is code-owned in `src/nebius_cxcli/validation_profiles.py`, mirroring the built-in wizard-profile and cluster-handoff layers. It is internal metadata, not a supported public catalog field.

Plugin default:

- Default runtime validation plugins are disabled.
- Operators can enable custom/provider-specific rule packs explicitly.

## Render Model

Infra render:

- Root-module Terraform layout with separated concerns:
  - `backend.tf`: authoritative remote-state backend config (root-owned; non-secret).
  - `versions.tf`: authoritative Terraform and provider constraints for generated root module.
  - `providers.tf`: provider configuration (child modules do not define provider blocks).
  - `variables.tf`: generated variable declarations for module arguments.
  - `main.tf`: module/resource orchestration only.
  - `outputs.tf`: generated root outputs required by higher-level CLI orchestration and declared component-output exports.
  - `terraform.auto.tfvars.json`: concrete values for generated variables, rendered locally and recreated from `generated/nebius-cxcli-manifest.json` by generated-bundle CLI commands before Terraform runs; config edits reach this file only through a new `render` that refreshes the manifest first.
- Generic module blocks from enabled infra module entries.
- `render` with source profile `portable` is the default and rewrites active local developer sources to `source.portable` when a matching portable source exists.
- `render` with source profile `local` preserves resolved filesystem module paths for workstation testing and is intentionally non-portable.
- `component_sources.yaml` and `component_cli_settings.yaml` are the checked-in catalog pair; build/package steps strip `source.local` from the bundled portable source catalog and bundle the settings catalog alongside it.
- Any app chart that still lacks `source.portable` remains intentionally local-only and fails portable release verification until a portable chart source is published.
- Release workflows rewrite internal `source.portable` refs from `?ref=main` to the current tag or commit before publishing.
- Generator-side commands use the global source profile to choose portable vs local output, and use `--component-sources-file` only when they need to override which catalog file is active.
- Deterministic output files:
  - `generated/nebius-cxcli-manifest.json`
  - `generated/infra/backend.tf`
  - `generated/infra/versions.tf`
  - `generated/infra/providers.tf`
  - `generated/infra/variables.tf`
  - `generated/infra/main.tf`
  - `generated/infra/outputs.tf`
  - `generated/infra/terraform.auto.tfvars.json` (ignored in git and recreated from the generated manifest by cxcli runtime commands)
  - `generated/infra/.terraform.lock.hcl` (generated by backend-disabled `terraform init -backend=false` during CLI `render` when Terraform is available)
- Remote-state backend is distinct from app/object-storage components:
  - Bucket/key/endpoint settings are derived from `client_info` (`client_name`, `project_id`, `region_id`).
  - `infra.components[id=object-storage]` remains workload/application storage only.
- Before backend-enabled Terraform init paths (`validate-generated`, `terraform plan`, `terraform apply`, `deploy`), CLI ensures the backend bucket exists via Nebius Storage API.
- Backend lock recovery remains available explicitly through `terraform unlock <generated-dir>`, which inspects the remote `.tflock` object for the rendered backend and then uses Terraform `force-unlock` only when the lock appears stale. By default it refuses to unlock while local Terraform/deploy operations are still active or when the recorded lock owner differs from the current local identity.
- `destroy` / `terraform destroy` can invoke that same stale-lock recovery automatically inside an already-confirmed destroy flow and retry Terraform destroy once before surfacing the lock failure.
- `terraform unlock` still requires `aws` CLI in `PATH`; Terraform itself may come from `PATH` or the managed Terraform download path.
- Local `deploy` validates the rendered Terraform root before apply, then resolves the rendered cluster ID output and prepares kubeconfig whenever a built-in handoff such as the bundled `mk8s` component is enabled. Flux work runs only when app charts are enabled.
- Customer-side commands operate on the rendered `generated/` bundle as the deploy contract and do not need the source catalog to recover local Terraform module paths from the original render machine.
- On non-CI local runs, that same built-in MK8s handoff also updates the user kubeconfig at `~/.kube/config` with a `nebius-cxcli` exec-based credential entry, so the target MK8s cluster is immediately usable with `kubectl` after `deploy`, `flux apply`, or `flux bootstrap` without a separate Nebius CLI install.
- Only `deploy`, `flux apply`, and `flux bootstrap` persist that local kubeconfig handoff. `destroy` and `flux destroy` use only a temporary kubeconfig when they need cluster access for rendered app teardown and should not switch the operator's local current-context as a side effect. Local multi-target runs now merge every selected target into `~/.kube/config` without overriding the existing `current-context`; only a single-target handoff switches the active context automatically.
- The built-in MK8s handoff no longer hardcodes public access. It resolves the endpoint choice from `inputs.mk8s_cluster_public_endpoint`, so the CLI selects the private API endpoint automatically when the cluster is configured private-only.
- Private-endpoint cluster access is supported, but reachability is still an environment concern. `nebius-cxcli` fails early with a targeted message when `kubectl` cannot reach a private control-plane endpoint; operators must provide that path through their own VPN, routed private network, tunnel, subnet router, or an in-network runner.
- Before `deploy`, `flux apply`, or `flux bootstrap` starts Flux work against a handed-off MK8s cluster, the CLI now prints a node-status snapshot and then proceeds directly into Flux or validation-specific readiness checks. The blocking waits are attached to the actual resources being reconciled rather than a generic "all nodes Ready" pre-gate. When no app charts are enabled, local `deploy` still prepares the handoff and persists local kubeconfig, but it skips Flux work entirely.
- Generated manifests can also carry deploy-time MK8s GPU validation specs. When present, local `deploy` still treats Terraform and Flux as the persistent reconciler layers, then runs the requested GPU checks against the handed-off cluster with `kubectl`, keeps machine-readable JSON detail reports under `generated/inventory/`, and refreshes one human-readable `generated/inventory/deploy-report.md` for the current run. That single Markdown artifact combines grouped `Infra`, `Apps`, `Grafana`, and `Validations` sections; its infra component status list is catalog-driven from `component_sources.yaml`, its MK8s rows use total-node wording for both CPU and GPU groups, and each validation with a JSON `checks[]` array renders those checks as a numbered Markdown list below the summary. For multi-target MK8s bundles it lists every cluster shape under `Infra` > `MK8s Clusters`, groups Grafana links per target, and keeps repeated validation headings target-scoped. When a run selects one target with `--target <instance-id>`, the refreshed validation section includes only that target's validations; `--all-targets` reports every selected target. The config contract stays on `deploy.targets[].validations.*`; the summary-file path is a fixed generated artifact rather than another project-level knob.
- Generated manifests are expected to carry `deploy.validations` metadata from `render`. Local `deploy` treats that metadata as part of the canonical generated-bundle contract and fails fast with rerender guidance when the field is missing or malformed instead of trying to recompute validations from the runtime config.
- That deploy-time MK8s GPU validation chain now keeps one continuous spinner active and updates its message from the emitted validation progress, so the CLI stays visibly alive while it transitions between operator-readiness, GPU-visibility, and NCCL phases.
- After that built-in MK8s handoff is prepared, the local Flux phase keeps one continuous spinner alive and updates its message across cluster reachability, Flux API discovery, rendered-manifest apply, and the final rendered-resource readiness wait so the command remains visibly active during quiet kubectl/Flux setup work.
- When no app charts are enabled, render writes an empty Flux kustomization with no placeholder Helm repository manifest. Local `deploy` still prepares the built-in handoff and refreshes local kubeconfig when available, but skips Flux work; on a multi-target infra-only bundle it refreshes every built-in cluster context so operators can switch between them locally after Terraform apply. `flux apply` continues to fail fast because there are no enabled charts to apply.
- In non-interactive environments, those same phase updates degrade to ordinary printed lines rather than transient spinner frames, so CI logs stay readable without requiring terminal animation support.
- `terraform plan` and `terraform apply` operate on the existing generated infra bundle rather than rerendering from `config.yaml`.
- `terraform apply` is a sequentially idempotent infra-only path for a given `generated/infra` bundle. Repeated runs converge through Terraform state; concurrent runs against the same backend are intentionally blocked by remote state locking.
- During long-running `terraform apply`, local `deploy` and `terraform apply` emit one merged status surface: Terraform apply transitions plus a light Nebius MK8s API snapshot. When an enabled `mk8s` component is present and Nebius SDK auth is available, the CLI polls Nebius MK8s API for cluster/node-group state; otherwise it falls back to an elapsed heartbeat for the API side.
- The merged status surface is formatted as a multi-line terminal block with separate TF and API sections so provider progress and Nebius API state are easy to distinguish during long creates.
- If Terraform apply fails, the CLI raises the Terraform failure as the canonical error and appends the last known merged Terraform/API status snapshot for context.
- If Terraform fails before it acquires the S3 backend lock, the CLI reports that as a backend lock failure, states that the run created nothing, and surfaces the lock owner/creation metadata Terraform returned. This avoids confusing a stale `.tflock` object with a cluster provisioning failure.
- If MK8s node-group status exposes `ERROR` events, the merged status block includes those alerts from the live SDK event objects so likely quota/provisioning problems surface before Terraform exits, and it prefers the event's human error text over raw SDK object reprs. Known transient bootstrap warnings are downgraded to notes while the node group remains in provisioning.
- If the live MK8s API reports an active terminal node-group error during apply or destroy, the CLI aborts the Terraform wait loop early and raises that API-side failure instead of burning the full generic Terraform timeout.
- Generated Flux artifacts are treated as deploy truth. If enabled app charts bind values from Terraform-backed component outputs, operators must rerender after the required Terraform state exists before treating `generated/flux` as the final GitOps payload.
- If Flux controllers are missing, local `deploy` installs the core Flux controllers into the target cluster from the official Flux install manifest before applying rendered resources. This removes the `flux` CLI dependency from local `deploy`.
- The Flux install manifest version used by local `deploy` comes from `component_cli_settings.yaml` `cli.flux.version`.
- After `kubectl apply -k generated/flux`, local `deploy` waits for the rendered Flux `source.toolkit` and `helm.toolkit` resources to become `Ready`, so a chart fetch/install failure does not get masked as a successful local deploy.
- The local Flux wait budget should remain resource-driven as well: when rendered workload resources declare `spec.timeout`, the CLI derives its default outer wait window from the longest rendered workload timeout plus a short grace period instead of assuming every chart fits in one fixed global window.
- During that Flux wait, local `deploy` and `flux apply` poll the rendered Flux resources from the cluster with `kubectl get -o json` and print a generic status block for the rendered `HelmRepository`, `GitRepository`, `HelmRelease`, and `Kustomization` objects. The status surface is resource-driven, not chart-specific.
- Flux `dependsOn` edges come from app `release.install_after` plus the MK8s GPU policy layer for context-specific role relationships such as `nvidia-network-operator -> nvidia-gpu-operator`.
- If one rendered workload reaches a terminal Flux failure while other rendered workloads are still progressing, the CLI keeps watching the remaining workload resources until they settle; it then exits non-zero with the failed-resource summary instead of waiting out the whole window on whichever source object happened to be listed first.
- If all rendered workload resources are already `Ready` and only rendered Flux source objects remain pending without any `Ready` condition, the CLI stops waiting and completes with a concise note. That guardrail avoids false hangs on source-controller status gaps after a successful local apply, and the note points operators at `kubectl get helmreleases.helm.toolkit.fluxcd.io -A` to verify workload release health directly.
- `deploy` and `flux apply` intentionally stay local direct-apply commands. They do not auto-bootstrap GitOps, because GitOps bootstrap has extra GitHub/Flux side effects. If the cluster is not bootstrapped yet, they now finish the local apply and print a warning with the exact `nebius-cxcli flux bootstrap <generated-dir>` follow-up command. The follow-up command uses the local generated bundle path; `flux bootstrap` resolves the GitHub repository from `GITHUB_REPOSITORY` or the local git `origin`, and the rendered `generated/flux` path must be committed and pushed before the cluster can continuously reconcile it.
- `flux apply` reuses that same local app-deploy path without Terraform apply, which makes it the apps-only command for day-2 chart deployments after infra is already present.
- `flux apply` is also sequentially idempotent for a given `generated/flux` bundle: it applies the current rendered manifests, skips Flux controller installation when the controllers already exist, and waits for the rendered Flux resources to report `Ready`.
- `flux bootstrap` auto-downloads a managed Flux CLI binary from the official Flux GitHub release for the catalog-pinned `cli.flux.version` when `flux` is not already available in `PATH`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- `flux bootstrap` resolves the GitHub repo slug from `GITHUB_REPOSITORY` when present, otherwise it falls back to the local git `origin` remote.
- `flux bootstrap` uses the same built-in MK8s handoff rather than hardcoding a specific Terraform output name in CI workflow logic.
- `flux bootstrap` only switches to reconcile mode when the cluster already contains both the core Flux controller deployments and the bootstrap Git objects `GitRepository/flux-system` plus `Kustomization/flux-system`. A cluster that only has Flux controllers from local `deploy`/`flux apply` is not treated as Git-bootstrapped yet.
- `flux bootstrap` is intentionally the GitOps path, not the direct-apply path. It assumes the rendered manifests are committed and pushed to the watched Git repository/path. `flux apply` is the local direct-apply path for immediate day-2 deployment before Git reconciliation is in place.
- GitOps safety comes from publishing one final watched-path snapshot, not from tearing Flux down. Normal updates should rerender locally, review the `generated/` diff, and push a single commit; do not publish an intermediate manifest-deletion commit and do not routinely unbootstrap/rebootstrap Flux to replace rendered artifacts.
- Local kubeconfig persistence is skipped automatically in CI and can be disabled explicitly with `NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG=false`.
- `flux bootstrap` still depends on GitHub release availability when the managed Flux CLI download path is used.

Managed vs external local tooling:

- Local developer bootstrap for this repo assumes Python `3.12+`, `make`, `git`, Python venv support, and a native build toolchain for Python-package fallback builds before `make venv` / `make all` are expected to work.
- The repo Makefile exposes an explicit fast/default unit lane plus separate integration/coverage entrypoints: `make test-unit`, `make test-integration`, and `make coverage`. The current suite still lives under `tests/`, so `test-unit` is the practical default while `tests/integration` remains the reserved isolated lane for future slower coverage.
- Provider lookup helpers should stay friendly to strict IDE type checkers as well as runtime checks; when `callable()` or optional-value narrowing is not enough for Pyright/Pylance, prefer explicit casts or stepwise typed locals over compact inference-heavy comprehensions.
- Auto-managed by the CLI when missing:
  - `terraform` for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output reads
  - `flux` for `flux bootstrap`
- Still external prerequisites:
  - `kubectl` for `deploy`, `destroy`, `flux apply`, `flux destroy`, `flux bootstrap`, and Flux readiness probes
  - Nebius SDK auth for kubeconfig generation against built-in cluster handoff components such as the bundled `mk8s`; the standalone `nebius` CLI is only an optional auth-token fallback, not a runtime dependency for cluster API access
  - `helm` for strict Helm source validation
  - `aws` CLI for `terraform unlock` remote lock inspection

Flux render:

- Generic Helm source docs (`HelmRepository` HTTP/OCI or `GitRepository` for standalone chart sources).
- Inventory artifacts are part of the canonical generated output set as well:
- `generated/inventory/deploy-report.md`
- `deploy-report.md` is the single human-readable customer report and the body used by the `email` command.
- The generated Markdown should stay lint-clean, including no trailing duplicate blank lines at EOF.
- `render`, `deploy`, `terraform apply`, `flux apply`, and `flux bootstrap` refresh that report artifact for the active project.
- Explicit Namespace docs for chart target namespaces.
- Generic HelmRelease docs from enabled app releases.
- Deterministic flat output under the rendered Flux tree:
  - app-only / external-cluster bundles use `generated/flux`
  - built-in cluster-target bundles use `generated/flux/targets/<target-id>`
  - each tree contains:
    - `helm-repositories.yaml`
    - `namespace-<namespace>.yaml`
    - `helmrelease-<group>-<release>.yaml`
    - `kustomization.yaml`
- Legacy nested Flux layout (`generated/flux/apps` and `generated/flux/sources`) is not supported.

## Auth and CI Bootstrap Model

`bootstrap-ci`:

- Generates workflow file.
- Treats `.github/workflows/nebius-deployments.yml` as a CLI-managed file and automatically reconciles it to the latest generated contract on every rerun.
- Requires the target config path to be inside the customer git repository so the workflow can be written at the repo root.
- Auto-detects the target GitHub repo from the checkout `origin` remote unless `--github-repo` overrides it.
- Fails before writing the workflow if GitHub reconciliation prerequisites are missing.
- Derives GitHub environment name as `<client_name>-<project_id>`, ensures that environment exists, then reconciles SMTP settings on every run and optionally Nebius CI auth secrets when `--auth-bootstrap` is enabled.
- Generated customer workflows validate with `nebius-cxcli validate-generated --portable` before `nebius-cxcli terraform plan` and `nebius-cxcli terraform apply` so non-portable local module paths are rejected in PRs and main-branch deploy runs, and the same generated-bundle strict readiness/quota preflight is enforced before those Terraform steps.
- Generated customer workflows also support manual `workflow_dispatch`; manual runs switch discovery to `nebius-cxcli discover --all <scope>` so every tracked project under the configured deployments scope is included even without a fresh git diff.
- If that configured deployments scope is the repository root, the workflow keeps discovery rooted at `.` and watches `*/*/generated/**` so the canonical two-level tenant/project layout still works without `./` in path filters.
- Generated customer workflows rely on the same generated-bundle CLI commands, which recreate ignored `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs. Raw Terraform from a fresh checkout is not the supported customer handoff path unless the operator first restores that ignored tfvars file and provides the same backend/auth environment.
- Generated customer workflows do not install the standalone `nebius` CLI; MK8s kubeconfig generation and token retrieval stay inside `nebius-cxcli` via the Nebius SDK.
- Generated customer workflows install `kubectl` directly from upstream Kubernetes release binaries instead of `azure/setup-kubectl`, avoiding GitHub Actions Node runtime deprecation coupling.
- Generated customer workflows also keep the Python runtime version in one env var and write compact single-line discovery JSON to `GITHUB_OUTPUT` for stable matrix handoff.
- Does not manage GitHub repo/org variables; `NEBIUS_CXCLI_REF` remains an optional manual override consumed by the generated workflow.
- `generated/infra/terraform.auto.tfvars.json` remains ignored in private deployment repos; generated-bundle CLI commands recreate it from `generated/nebius-cxcli-manifest.json` before Terraform runs so CI does not depend on a committed tfvars file or duplicate that restore logic in workflow YAML.

`auth`:

- Reads `~/.config/nebius-cxcli/<client_name>-<project-id>/runtime-auth.json`.
- Uses one runtime target mode at a time: `--project-config <config.yaml>` resolves
  both `project_id` and `client_name`, while the manual path uses `--project-id`
  plus `--client-name` when the cache cannot infer a unique client. Omitting both
  target options is allowed only for global `--validate-profile`.
- `--create`: creates runtime auth profile if cache is missing; otherwise no rotation.
- `--recreate`: always rotates keys and refreshes cached material.
- `--validate-profile`: checks local private key presence and verifies auth public key visibility via Nebius IAM API.
  When no project/config target is provided, it validates every cached runtime auth profile.
- `--auto-auth-bootstrap` command paths also recreate a cached runtime auth profile automatically
  when the cached Nebius auth public key has been deleted or the cached private-key metadata is
  broken, but they do not rotate a healthy cached profile.
- Runtime-auth metadata writes use same-directory temporary files plus atomic replace so
  a failed write does not leave a partially written `runtime-auth.json`.
- ESO MysteryBox does not use the local runtime-auth cache. The configured Kubernetes
  Subject Credentials Secret is the persisted ESO auth location. Deploy/Flux commands
  create or replace that Secret only when it is missing, invalid, references a different
  service account than `mysterybox-sa`, or references a Nebius authorized public key that
  is no longer readable.
- Stale-profile IAM verification and runtime-auth bootstrap use short-lived Nebius SDK clients.
  cxcli closes those clients after each check/create step. After creating new runtime auth
  keys, it waits until Nebius token exchange accepts the new public key before handing control
  to Terraform backend/apply work. During stale-profile recovery and that propagation wait,
  cxcli suppresses only the expected first-attempt deleted-key token-refresh traceback while it
  converts the SDK failure into the canonical warning/retry path.
- `--bootstrap-ci`: syncs local cached auth material into GitHub environment secrets (`<client_name>-<project_id>`); requires existing local cache material.

Terraform runtime auth:

- Generated `providers.tf` uses direct Nebius provider service-account fields and `module_name`.
- Runtime auth material is passed to Terraform via `TF_VAR_*` rather than provider `_env` fields.
- Runtime auto-bootstrap uses dedicated service account name `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/<client_name>-<project-id>/`.
- ESO MysteryBox auth is deliberately separate from the Terraform runtime cache. Rendered
  Git bundles never carry the Subject Credentials Secret; deploy/Flux commands manage that
  runtime-only Kubernetes Secret directly for ESO.
- Terraform backend path requires AWS-compatible Object Storage keys (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`); runtime auth cache provides them automatically when bootstrapped.

## Vendor Scope

Current runtime implementation is Nebius-focused:

- Nebius SDK/API integration for auth/IAM and provider option lookups.
- Nebius-oriented defaults for provider/config behavior.

The component source model itself is Terraform-module + Helm-chart based, but this release does not claim full multi-vendor runtime support.

## Runtime Versioning

- Installed wheels rely on package metadata for the published version.
- Source/editable checkouts prefer live SCM state over a generated `_version.py` cache: they use `setuptools-scm` when available and fall back to `git describe` when it is not, so local runtime behavior still tracks the current repo state even in minimal release-shell environments.
- `publish-release.sh --prep X.Y.Z` fails before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`, so duplicate release-prep runs stop before producing a redundant changelog commit.
- `publish-release.sh --prep X.Y.Z` is otherwise idempotent while the target tag remains unreleased: once `Unreleased` is empty, reruns leave `CHANGELOG.md` and `HEAD` unchanged.
- `publish-release.sh --publish X.Y.Z` creates the service tag locally, verifies that the tagged source checkout resolves `nebius_cxcli.__version__ == X.Y.Z`, and only then pushes the tag to trigger the release workflow.

## Source Code Structure

- `setup.py`: wheel-build hook that bundles the portable `component_sources.yaml` view and rewrites internal release refs for published artifacts.
- `src/nebius_cxcli/cli.py`: CLI entrypoints, orchestration, strict checks, command behavior.
- `src/nebius_cxcli/component_sources.py`: source registry loading, strict schema parsing, source-profile resolution, automatic Terraform output export, and `wizard_profile` expansion/merge.
- `src/nebius_cxcli/cluster_handoffs.py`: built-in cluster handoff contracts such as the bundled `mk8s` kubeconfig/bootstrap handoff.
- `src/nebius_cxcli/validation_profiles.py`: built-in runtime validation-profile defaults for bundled infra components.
- `src/nebius_cxcli/wizard_profiles.py`: built-in one-to-one infra `wizard_profile` registry for bundled component-guidance shorthands.
- `src/nebius_cxcli/component_defaults.py`: shared/default resolution and virtual prompt-default seeding for source-catalog values.
- `src/nebius_cxcli/component_wiring.py`: producer-to-consumer Terraform output binding helpers.
- `src/nebius_cxcli/components.py`: runtime component entry generation and dependency helpers.
- `src/nebius_cxcli/config_template.py`: starter `config.yaml` generation from runtime entries.
- `src/nebius_cxcli/config_model.py`: runtime/dynamic shape conversion.
- `src/nebius_cxcli/config_loader.py`: file loading + runtime validation normalization.
- `src/nebius_cxcli/runtime_validation.py`: core runtime validation.
- `src/nebius_cxcli/runtime_plugin_validation.py`: optional validation plugin loader.
- `src/nebius_cxcli/runtime_component_validation.py`: optional component rule plugin (not default-loaded).
- `src/nebius_cxcli/runtime_introspection.py`: module/chart introspection helpers.
- `src/nebius_cxcli/provider_options.py`: Nebius/provider-backed field option lookup, built-in provider source registry, plugin hooks, option filtering, and resolver error reporting.
- `src/nebius_cxcli/sdk_auth.py`: shared Nebius SDK initialization used by auth/bootstrap and provider-backed option lookups.
- `src/nebius_cxcli/infra_render.py`: Terraform render generation.
- `src/nebius_cxcli/terraform_backend.py`: Terraform remote-state backend derivation/rendering + bucket bootstrap.
- `src/nebius_cxcli/flux_render.py`: Flux render generation.
- `../../skills/onboard-nbs-cxcli/SKILL.md`: central Codex skill for onboarding Nebius Terraform modules into `nebius-cxcli`, including when to stop at `component_sources.yaml` and when to touch code-owned onboarding layers.
- `src/nebius_cxcli/render.py`: combined render orchestration.
- `src/nebius_cxcli/terraform_ops.py`: terraform command wrappers.
- `src/nebius_cxcli/flux_ops.py`: flux bootstrap/reconcile wrappers.
- `src/nebius_cxcli/discover_ops.py`: changed-config discovery (git and non-git modes).
- `src/nebius_cxcli/iam_bootstrap.py`: Nebius IAM bootstrap (identity + key material).
- `src/nebius_cxcli/github_secrets.py`: GitHub repo/environment secret sync helpers.
- `src/nebius_cxcli/paths.py`: project path resolution and alignment checks.
- `src/nebius_cxcli/generated_manifest.py`: generated-bundle manifest read/write helpers for deploy/runtime replay.
- `src/nebius_cxcli/email_settings.py`: operator-local SMTP/email settings persistence and resolution.
- `src/nebius_cxcli/inventory_ops.py`: deploy report generation operations.
- `src/nebius_cxcli/notify_ops.py`: email notification operations.
- `src/nebius_cxcli/managed_tools.py`: managed Terraform/Flux download and cache helpers for tool bootstrap.
- `component_sources.yaml`: repo-level starter source registry editable by operators.
- `component_cli_settings.yaml`: repo-level cxcli settings registry linked to the source registry by component id.
- `<install-prefix>/nebius_cxcli/component_sources.yaml` and `<install-prefix>/nebius_cxcli/component_cli_settings.yaml` (wheel data-files): bundled fallback registries shipped inside wheel builds.
- The `nebius-cxcli` GitHub release workflow publishes both the wheel and the raw portable catalog file so operators can download the editable source catalog directly from the release page with module refs already pinned to the published release tag.
- The repo CI and release workflows run the same local `make all` verification contract before wheel verification or release publication so GitHub Actions and local development stay on one lint/test/build path.
- That `make all` path intentionally reuses the repo `.venv` for `python -m build --wheel --no-isolation` and overlaps the wheel build with the lint/test gate after env setup instead of paying for a second isolated build environment on every run; `make venv` upgrades `setuptools` first so the shared environment satisfies the build backend contract.
- After `make all`, the repo CI workflow runs `validate-sources component_sources.yaml` with source profile `local` so branch changes are checked against the current checkout's Terraform modules and Helm charts. The release workflow separately runs the same command with source profile `portable` so published wheels and release catalogs are still verified against portable pinned sources.
- The repo CI workflow checks that built wheels bundle both `component_sources.yaml` and `component_cli_settings.yaml` with a valid catalog shape. The release workflow is the place that runs the stricter portable `verify-wheel` / `verify-catalog` checks.
- Post-`make all` workflow verification uses the repo `.venv/bin/python` for `nebius_cxcli.release_catalog` commands so wheel/catalog checks import the checked-out editable package reliably under GitHub Actions.

Primary automated test ownership:

- `tests/test_cli.py` and `tests/test_cli_command_coverage.py`: CLI command contract and workflow-generation behavior.
- `tests/test_component_sources.py`: component source precedence and validation rules, including `validate-sources` registry checks.
- `tests/test_components_runtime_discovery.py`: component-entry discovery from source catalogs, including bundled `wizard_profile` expansion on runtime entries.
- `tests/test_wizard_provider_field_specs.py`: explicit wizard/provider wiring behavior, relative `depends_on` normalization, and provider-backed allowed-value semantics.
- `tests/test_provider_option_plugins.py`: provider-option plugin hooks, plugin filtering, and provider error reporting behavior.
- `tests/test_github_secrets.py`: GitHub repo/environment secret helper behavior, including environment creation and environment-secret upsert orchestration.
- `tests/test_setup_build.py`: setup/build packaging contract, with CI build env isolated so source selection and release-ref rewrite precedence stay deterministic under GitHub Actions.
