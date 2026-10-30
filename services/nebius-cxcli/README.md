# nebius-cxcli

`nebius-cxcli` creates and manages Nebius infrastructure and applications from a
project `config.yaml`. It generates Terraform and Flux artifacts, deploys them,
and provides MK8s, Soperator, Grafana, and access-management commands.

## Contents

- [Installation](#installation)
  - [Credentials and tools](#credentials-and-tools)
- [Quick start](#quick-start)
- [Core concepts](#core-concepts)
- [Commands by category](#commands-by-category)
  - [Projects and configuration](#projects-and-configuration)
  - [Quotas and capacity](#quotas-and-capacity)
  - [Deployment, Terraform, and Flux](#deployment-terraform-and-flux)
  - [MK8s](#mk8s)
  - [Soperator](#soperator)
  - [Helm-chart (apps) upgrade](#helm-chart-apps-upgrade)
  - [Grafana](#grafana)
  - [VM access](#vm-access)
  - [Authentication, CI, and reporting](#authentication-ci-and-reporting)
- [Security and operational essentials](#security-and-operational-essentials)
- [Detailed guides](#detailed-guides)
- [Development](#development)

## Installation

Use Git, an installed Python **3.12, 3.13, or 3.14**, and uv **0.13.5 or a
compatible 0.13.x release**. Install the CLI with `uv tool` to run
`nebius-cxcli` from any directory without activating a virtual environment.

If uv is not installed, install it with an existing, working `pipx`:

```bash
pipx install 'uv>=0.13.5,<0.14'
```

If `pipx` is unavailable, use an installation method from the
[official uv guide](https://docs.astral.sh/uv/getting-started/installation/).
Ensure `uv --version` works in your terminal before continuing.

From the directory where you want to keep the checkout:

```bash
git clone https://github.com/nebius/nebius-ps-services.git
uv tool install --python 3.12 --no-python-downloads \
  --editable ./nebius-ps-services/services/nebius-cxcli
uv tool update-shell
```

The example selects an already-installed Python 3.12; use `3.13` or `3.14` if
that is your installed version. If you already have a checkout, skip the clone
and use its `services/nebius-cxcli` path. Keep that checkout at the same path:
editable installation uses its source directly, so source edits affect the CLI.
After dependency or package-metadata changes, rerun the installation command
with `--reinstall`.

Open a new terminal after shell setup, then verify the installed command:

```bash
nebius-cxcli --version
nebius-cxcli --help
```

Or install a chosen repository branch or release tag. Replace `BRANCH_OR_TAG`
before running this alternative installation command:

```bash
uv tool install --python 3.12 --no-python-downloads \
  "git+https://github.com/nebius/nebius-ps-services.git@BRANCH_OR_TAG#subdirectory=services/nebius-cxcli"
uv tool update-shell
```

This option does not depend on retaining a local checkout. Open a new terminal
and run the same version/help checks afterward. Contributor setup is covered in
the [development guide](docs/development.md).

### Credentials and tools

Commands that query or change Nebius resources need a supported operator
profile or authentication environment. Project-aware commands can create or
reconcile the canonical project service account and renewable credentials on
first use. See [authentication](docs/project-workflows.md#authentication).

| Tool or access | When needed |
| --- | --- |
| Nebius API access | Live discovery, quota checks, validation, rendering, deployment, and lifecycle operations |
| `kubectl` and access to the selected Kubernetes API | Cluster validation, app deployment, Soperator operations, profiling, and acceptance tests |
| `helm` | Chart source validation and official Soperator release admission; ordinary app operations on Soperator also inspect release ownership |
| `terraform` | Terraform-backed operations; supported paths download the catalog-pinned binary if absent |
| `flux` | GitOps bootstrap; downloaded at the catalog-pinned version if absent |
| `aws` | Guarded Terraform unlock |
| `git` and GitHub access | CI bootstrap, repository discovery, Git chart sources, and GitOps bootstrap |
| Nebius identity with SecretStash payload access | WireGuard day-2 operations |
| SSH access and a verified known-hosts file | SSH jump-host day-2 operations |

Managed tool downloads need access to official HashiCorp or GitHub releases
and verify published checksums. Keep `kubectl` within the target server's
[supported version skew](https://kubernetes.io/releases/version-skew-policy/).
For a private Kubernetes endpoint, establish the network path before running
commands that connect to it. **MK8s `destroy --target CLUSTER_ID` uses cloud APIs
and does not need Kubernetes API access.**

A standalone `nebius` CLI is not required for ordinary operation; cxcli uses the
Nebius Python SDK. Project, Tenant, and AccessKey calls use IAM v2 directly;
other services use their supported API versions independently. Project scope
validation requires the exact project ID and parent tenant ID, without requiring
tenant-read permission. Optional tenant/project names fall back to resource IDs
for folder naming when unavailable. Automatic quota-request submission is a
separate internal operator capability described below.

## Quick start

Create a project interactively, select its infrastructure and apps, and use the
configuration path printed by the wizard:

```bash
nebius-cxcli create ./deployments
```

Set `CONFIG` to that file. The path below is illustrative; use the actual
folder names created by the wizard.

```bash
CONFIG=./deployments/tenant/project/config.yaml
GENERATED="$(dirname "$CONFIG")/generated"

nebius-cxcli validate "$CONFIG"
nebius-cxcli render "$CONFIG"
nebius-cxcli validate-generated "$GENERATED"
nebius-cxcli deploy "$CONFIG"
```

`validate` includes deployment-readiness and live quota/network checks. Resolve
reported shortages or configuration errors before continuing. Inspect the
result under `generated/reports/deploy-report.md`.

For a fresh **Soperator** cluster, start with `soperator create` instead of
`create`, then use the same validation/render/deploy sequence. For an existing
Soperator cluster, use `soperator onboard`; see [Soperator](#soperator).

After editing `config.yaml`, rerun `validate` and `render` before deployment.
`deploy` consumes the existing rendered bundle; it does not rerender your edits.
This differs from recovering an interrupted operation, which requires its
**original** generation and execution options.

## Core concepts

Configurations require `version: v2`. Infrastructure components own their
observability, secrets integration and cluster checks. External clusters live in
`infra.external_clusters`. Soperator application rows own smoke checks, rollout,
profiling and registration. The old top-level `deploy` section is rejected.
An omitted or null `observability` block uses the same default behavior.
See the [ownership and cutover guide](docs/configuration-reference.md#project-configuration)
before upgrading an existing project.

| Concept | Meaning |
| --- | --- |
| `config.yaml` | Desired project identity, component instances, app values, and target settings |
| `generated/` | Rendered deployment contract, including the manifest snapshot and artifacts |
| Component ID | Reusable component type, such as `mk8s`, `vm`, or `grafana` |
| Instance ID | One configured instance; app instance IDs identify their cluster target |
| Target ID | Logical cluster identity used by most deployment/app commands |
| Cluster ID | Immutable Nebius cloud ID; required by MK8s destruction and raw cloud discovery |

```text
config.yaml
  |
  +-- generated/infra/       Terraform: resources outside the cluster
  +-- generated/flux/        Flux and Helm: applications inside the cluster
  +-- generated/reports/     Deployment results and operation reports
```

Do not use Terraform as an in-cluster package manager. App charts belong to
enabled built-in MK8s targets or onboarded Nebius MK8s targets in the project.
An app-only bundle without a cluster target is unsupported.
Infrastructure-only bundles, such as VPC networks and subnets with no cluster
targets or apps, complete `deploy` after Terraform convergence and local baseline
publication. They do not require Kubernetes access. Without an applicable API
watcher, deployment status reports Terraform progress only; this is not an API
connection failure.

Use `component list/add/remove` for day-2 selection changes. `create` starts a
project from scratch; overwriting an existing project can remove **all files in
that resolved project folder**. `render` replaces generated artifacts after a
complete staged render; it does not itself destroy live infrastructure.

Target syntax is command-specific:

- App/deploy commands generally use `--target TARGET_ID`.
- MK8s upgrade/migration uses the positional selector `infra:mk8s@TARGET_ID`.
- MK8s destroy uses `--target CLUSTER_ID`, even for a single cluster.
- Components use selectors such as `infra:vm` or `apps:grafana@TARGET_ID`.

Cluster operations verify the selected target's identity. The workstation's
`current-context` does not choose the destination. Multiple targets can require
an explicit selector; consult the command's help before automation.

## Commands by category

Prefix every command below with `nebius-cxcli`. `CONFIG_YAML` means the project
configuration file; `GENERATED_PATH` means its rendered `generated/` directory.
Uppercase IDs and example paths are placeholders to replace with your values.
Use `nebius-cxcli COMMAND --help` for all flags, defaults, and examples.

<!-- command-index:start -->

### Projects and configuration

| Command | Input | Purpose |
| --- | --- | --- |
| `create` | Deployments root | Create one project through the guided wizard |
| `discover` | Deployments root | Discover local projects for deployment/CI; `--all` ignores the Git change filter |
| `component list` | `--config CONFIG_YAML` | List selected components |
| `component add` | Optional selectors and `--config CONFIG_YAML` | Add multiple infrastructure components and target-bound apps through the wizard |
| `component remove` | Selectors and `--config CONFIG_YAML` | Remove component selections from configuration |
| `validate` | `CONFIG_YAML` | Check configuration, readiness, networking, and live quota/capacity |
| `validate-generated` | `GENERATED_PATH` | Validate the rendered bundle |
| `validate-sources` | Optional source-catalog path | Validate Terraform/Helm sources and catalog wiring |
| `render` | `CONFIG_YAML` | Generate the deployment bundle |

```bash
nebius-cxcli component list --config "$CONFIG"
nebius-cxcli component add --config "$CONFIG"
nebius-cxcli component add infra:vm --config "$CONFIG"
nebius-cxcli validate "$CONFIG"
nebius-cxcli render "$CONFIG" --force
```

With no selectors, `component add` always shows Infra then Apps multi-select
lists. Choose multiple components with Space and Enter, or use comma-separated
IDs/indexes in the text prompt (`all` and `none` are also supported). Leave either
section empty to skip it; leaving both empty makes no changes. Backtracking
retains confirmed choices. The full field wizard configures the additions while
preserving existing component settings. Apps require an existing or newly
selected MK8s target; Soperator uses its dedicated command group. The command
saves configuration only, so run `validate` and `render` before deployment.

Infrastructure names confirmed before the field wizard are reused without a
second name prompt. This includes WireGuard gateways and nested scalar names
such as MK8s cluster names; the saved name and component instance ID stay aligned.

New ordinary MK8s targets default to **Nebius-managed observability**. The
**In-cluster observability** choice installs VictoriaMetrics, VictoriaLogs,
VictoriaTraces and Grafana locally; **Disabled** stops cxcli collection. Managed
mode installs only the Nebius collector, using the configured project and region.
Explicit per-signal opt-outs remain disabled across render and deploy; turning a
signal back on reuses its saved route. Selecting the named managed mode resets
custom write routes to Nebius defaults while retaining separately named query sources.
Deployment requires current-run backend and Grafana query evidence before reporting
success. Managed verification uses an explicit human `NEBIUS_PROFILE` with access
to the project's datasources on `https://grafana.nebius.dev/`.
See [observability](docs/observability.md#creation-wizard-defaults) for setup,
application instrumentation, validation and retained-store behavior.

Bucket labels are optional metadata: the wizard asks whether to add labels, then
collects names and string values without YAML. Existing labels can be kept,
edited or cleared. WireGuard shows its effective defaults: create a public IP
`true`, client keepalive `25` seconds, and NAT for access to private resources
`true`. Reusing an IP requires its allocation ID. The allocation name defaults
to `<gateway-name>-public-ip` and follows gateway renaming unless overridden.
Keepalive accepts whole seconds from `0` to `65535`; `0` disables it. Disabling
NAT requires return routes to VPN clients. Switching from IP reuse to creation
clears the conflicting allocation ID. Existing name overrides offer Keep,
Change, or Use automatic name.

WireGuard's `local_subnets` are private destination IPv4 CIDRs routed through
the VPN by client `AllowedIPs`, separate from the gateway's placement subnet.
The wizard lists current subnets through the Nebius SDK in the selected VPC,
with names, IDs and CIDRs. Shared network pools are labelled because their
ranges can cover multiple subnets. New destinations start unselected; saved
routes remain selected. Manual entry supports planned or otherwise reachable
networks. An empty selection gives tunnel-only access. See
[WireGuard setup](docs/wireguard.md#vpn-destination-subnets).

Review removals and the generated diff before applying them. `render --force`
approves replacement of local generated artifacts, not a live-resource plan.

For local source profiles, bundled WireGuard, SSH jump-host, NFS and NAT gateway
modules retain their containing package through `platform-infra//modules/...`
source addresses. This lets Terraform initialize their shared sibling VM module.
After updating the catalog, rerun `render` to refresh existing generated sources.

The MK8s wizard offers None, Label, Taint, or Both for each new node group and
saves native `node_labels` and `taints` in `config.yaml`. When adding SFS and
MK8s together, ordinary wizards configure shared storage before node groups.
Each new group lists eligible filesystems by name under **Attach shared storage
to node group**, with all selected initially. Uncheck any or all; the text prompt
accepts indexes or `none`. This configures node attachments; mounting, CSI and
PV/PVC setup remain separate. Managed VPC choices
show confirmed existing resources once while preserving their bindings;
unresolved ownership blocks automatic selection. See [cluster creation and
placement](docs/mk8s.md#cluster-creation-and-node-placement).

```bash
nebius-cxcli discover ./deployments --all
nebius-cxcli validate-sources ./component_sources.yaml
```

Root `discover` reads local Git/filesystem state; it is different from live
`soperator discover`. Catalog fields and configuration examples are in the
[configuration reference](docs/configuration-reference.md).

### Quotas and capacity

| Command | Input | Purpose |
| --- | --- | --- |
| `quota-check` | `CONFIG_YAML` | Assess enabled infrastructure against live quotas and capacity |
| `quota-request` | `CONFIG_YAML` | Submit confirmed shortages where supported, otherwise print manual request targets |

```bash
nebius-cxcli quota-check "$CONFIG"
```

Run `quota-request` when you intend to request additional quota. It assesses
shortages itself and can submit requests **without prompting**. Automatic
submission needs the internal `npc` CLI, Nebius CLI token acquisition, and
Nebius internal-network/operator access; other environments receive manual
request guidance.

`validate`, `quota-check`, `quota-request`, `render` and generated deployment
preflight check retained standalone filesystems against a fresh Terraform plan
when generated infrastructure and its manifest are available. The generated
filesystem inputs and Nebius scope must match the current configuration;
changes to unrelated components do not remove filesystem credit.
Filesystem retention uses a separate plan scoped to its exact module, so an
unresolved first-deployment gateway bootstrap cannot discard that proof. This
observation plan is never used to apply infrastructure.
Unchanged filesystems need no additional quota;
in-place growth needs only the increase. New and replacement filesystems still
need their full quota. Missing generated artifacts, edited filesystem inputs,
stale planned sizes and unresolved plan evidence receive no credit.

A separate fresh plan credits retained public and private address allocations
owned by unchanged WireGuard and NAT components. New or replacement allocations
still need their full quota. NAT planning uses its read-only deployment preview
to prepare runtime inputs; it does not activate routes or recover an interrupted
activation. Complete interrupted NAT recovery through `deploy` before previewing.

Inspection and planning commands do not apply desired infrastructure, but
project authentication setup and local runtime preparation can still occur.
They should not be treated as universally side-effect-free.

### Deployment, Terraform, and Flux

| Command | Input | Purpose |
| --- | --- | --- |
| `deploy` | `CONFIG_YAML` | Apply the sibling generated bundle and run its required checks |
| `terraform plan` | `GENERATED_PATH` | Inspect infrastructure changes using cxcli runtime/backend wiring |
| `terraform apply` | `GENERATED_PATH` | Apply generated Terraform infrastructure |
| `terraform destroy` | `GENERATED_PATH` | Tear down eligible projects without MK8s |
| `terraform unlock` | `GENERATED_PATH` | Inspect and, after guarded confirmation, remove an eligible stale lock |
| `tailscale onboarding request` | `CONFIG_YAML`, optional export directory | Print the exact administrator handoff without credentials |
| `tailscale onboarding configure` | `CONFIG_YAML` | Guided approval and credential-reference handoff before activation |
| `tailscale auth configure` | `CONFIG_YAML` | Store credential references |
| `tailscale auth check` | `CONFIG_YAML` | Check delegated tailnet access |
| `tailscale policy plan` | `CONFIG_YAML`, output path | Prepare a reviewed HuJSON policy proposal |
| `tailscale policy apply` | `CONFIG_YAML`, proposal and digest | Conditionally apply the reviewed proposal |
| `tailscale routes approve` | `CONFIG_YAML` | Approve exact prefixes on owned devices |
| `tailscale status` | `CONFIG_YAML` | Inspect live readiness |
| `tailscale doctor` | `CONFIG_YAML` | Diagnose cloud, guest and tailnet prerequisites |
| `tailscale verify` | `CONFIG_YAML`, optional `--failover` | Prove client HTTPS traffic and optionally bounded failover |
| `tailscale recover` | `CONFIG_YAML`, exact lock owner or enrollment operation/member | Recover a stopped writer or resume/fence enrollment |
| `nat status` | `CONFIG_YAML` | Observe gateway, routing and optional trusted-SSH readiness |
| `nat refresh-routes` | `CONFIG_YAML` | Refresh detached source policy snapshots in configuration |
| `nat unlock` | `CONFIG_YAML` and exact owner | Release an abandoned shared NAT lock after writers stop |
| `nat reconcile` | `CONFIG_YAML` | Reconcile settled ambiguous attachments without changing routes |
| `flux apply` | `GENERATED_PATH` | Apply apps directly and wait for readiness |
| `flux bootstrap` | `GENERATED_PATH` | Establish or reconcile continuous GitOps delivery |
| `flux destroy` | `GENERATED_PATH` | Remove eligible rendered app resources |

```bash
nebius-cxcli terraform plan "$GENERATED"
nebius-cxcli deploy "$CONFIG"
```

Use `deploy` for initial Soperator installation and protected changes. Dedicated
Terraform mutations and Flux bootstrap/destroy reject Soperator targets.

Managed CPU-only MK8s targets with no enabled apps or in-cluster integrations
use Nebius API readiness checks, including private-only clusters. They require
cloud API access, without a local route to the Kubernetes endpoint. Reports
verify the cluster and node groups and explicitly leave Kubernetes workloads
uninspected. Executed provider checks appear in the required validation report;
deployment succeeds only when that saved report passes. App, GPU, external, and locally app-bound targets still require
Kubernetes access; see [private-cluster deployment](docs/mk8s.md#private-cluster-deployment).

The opt-in [Tailscale subnet router](docs/tailscale.md) provisions two persistent
Nebius VMs by default and connects any customer-owned hosted tailnet. Select
`tailscale-gw` in the standard wizard, then use ordinary validate/render/deploy
for infrastructure readiness. `tailscale onboarding request` prints the exact
administrator request; `onboarding configure` records the approved handoff before
a second render/deploy activates it. OAuth custody can use direct SDK SecretStash
access, including renewable Kubernetes workload identity, without requiring ESO.
Interrupted enrollment resumes its original operation/key through authenticated
guest receipts and requires the current approved intent, as does route approval.
Independent retirement handles partially provisioned members without
enrolling them. Tailnet configuration is organization-neutral.
An administrator provides the [approved tag and scoped credential](docs/tailscale.md#administrator-handoff-manual-prerequisites)
once; cxcli automates the permitted provisioning, enrollment, route approval,
verification and retirement steps. Administrator-only policy or signing approvals
remain explicit prerequisites.
All tailnet operations are under `nebius-cxcli tailscale`; no parallel installer or
deployment entry point is introduced. Policy writes and failover tests are explicit.
Direct Terraform apply remains blocked while Tailscale resources exist in state,
even after their component is disabled or removed; use ordinary lifecycle retirement.
Policy commands resolve linked SecretStash containers from infrastructure outputs
without modifying the reviewed policy intent.

The opt-in [custom NAT gateway](docs/nat-gateway.md) also uses `deploy` for staged
provisioning, trusted guest readiness, route activation and restoration. Its
normal wizard saves activation-enabled YAML without a separate activation
question. Validate and render the saved configuration, then deploy provisions
the gateway, verifies readiness, activates routes and runs tests if enabled.
Route activation accepts provider metadata recomputation while rejecting changes
to subnet identity, CIDRs, configured pool selection and other authored fields.
For fresh gateways, deployment generates a pinned host identity before provisioning
and passes it through protected runtime inputs. It recovers that identity through
authenticated Compute metadata to populate `generated/ssh_known_hosts`; SSH trust
does not use serial logs. Existing pins remain unchanged across render. Previously deployed
gateways with guest-generated keys retain their VM and require independently
verified keys in that file or through `--ssh-known-hosts-file`.
The final route summary is informational; disabling tests still activates routes.
Explicit `routing.enabled: false` remains available in YAML for operational
preparation or detachment; completing the wizard again enables routing. The
wizard discovers CPU-only presets and live quotas; defaults prefer
`cpu-d3` / `4vcpu-16gb` and a 40 GiB boot disk. After VPC selection, one gateway
subnet picker lists suitable existing/planned subnets alongside dedicated creation;
creation remains the default (new `/28`), and valid saved selections are preserved.
Then select private workload subnets; each receives its own route table preserving
non-default routes. Existing managed subnet rows omit internal ownership annotations.
NAT SSH accepts any IPv4 source using an authorized key and temporary fail2ban
blocks for repeated authentication failures. The wizard has no `admin_cidrs`
prompt; existing gateways follow the guide's detached upgrade procedure.
Backtracking and exact identity reconciliation preserve original route snapshots,
and new workload selections are explicit. Incomplete VPC inventory requires retry;
the gateway remains excluded from workload choices across refreshes.
Pending probe cleanup blocks retirement even after routes are detached.
Terraform owns permanent resources. Default-selected automatic tests use sequential
SDK-created private probe VMs and restore this activation on failed or inconclusive
results. Deploy automatically configures pinned SSH access to each private test VM
through the gateway, checks its outbound public IP, and confirms cleanup. Tests
also run when Terraform has no changes. Serial-log access and manual test setup
are not required; see the linked guide for the verification contract. Address-space
previews allow well-formed drafts; deployment requires confirmed contiguous
space for the fresh plan's combined reservations. Retained subnets and
route-only restoration do not require spare capacity.
Planned growth of a derived pool must also fit its upstream pool reservations.
After a successful accepted Soperator deployment, ordinary app changes can use:

```bash
nebius-cxcli render "$CONFIG" --force
nebius-cxcli flux apply "$GENERATED" --target TARGET_ID
```

This requires unchanged protected Soperator settings and a valid accepted
baseline. It does not invoke Slurm maintenance or apply Terraform.

`deploy` and `flux apply` perform local direct application. They do not enable
continuous GitOps automatically. For eligible targets, commit and push the
rendered Flux tree to its watched repository before using `flux bootstrap`.

**Recovery:** fix the reported cause, then rerun `deploy` with the original
rendered generation and execution controls. Do not rerender an interrupted
operation or edit its receipts. Preserve the original target, skip controls,
and job timeout options. Local checkpoints bind those semantic controls;
separate machines/CI runners must serialize workflows for the same backend.

See [deployment and recovery](docs/project-workflows.md#deployment-and-recovery)
for path contracts, multi-target behavior, locking, and recovery details.

### MK8s

| Command | Input | Purpose |
| --- | --- | --- |
| `upgrade node-template` | `CONFIG_YAML` and optional guided target selector | Upgrade Kubernetes, node OS, and Nebius-image GPU stack |
| `migrate node-group` | `CONFIG_YAML` and `infra:mk8s@TARGET_ID` | Permanently replace a node group for hardware or placement changes |
| `destroy` | `CONFIG_YAML`; MK8s requires `--target CLUSTER_ID` | Delete one MK8s cluster, or tear down a project without MK8s |
| `acceptance-test smoke` | `CONFIG_YAML` and `--suite` | Run the selected post-deployment functional workload suite |
| `acceptance-test benchmark` | `CONFIG_YAML` and `--suite` | Run the selected benchmark workload and report results |

Preview an upgrade before executing it. Choose versions supported by the live
provider and selected target:

```bash
nebius-cxcli upgrade node-template "$CONFIG" infra:mk8s@TARGET_ID \
  --to-version 1.33 --dry-run
```

`upgrade node-template` changes software, not hardware platform, hardware
preset, GPU-cluster, or fabric. Use `migrate node-group` for a dedicated permanent
hardware replacement. Review surge capacity, disruption policy, and final
readiness requirements in the [MK8s guide](docs/mk8s.md).

```bash
nebius-cxcli acceptance-test smoke "$CONFIG" \
  --suite k8s-cuda --target TARGET_ID
nebius-cxcli acceptance-test benchmark "$CONFIG" \
  --suite k8s-nccl --target TARGET_ID --max-nodes 2 --timeout 30m
```

These suites create temporary cluster workloads and write local reports. They
are explicit post-deploy tests; they can consume GPUs and affect available
capacity. Suite eligibility and transport depend on the selected target.
Omitting `--target` runs across generated targets.

Preview cluster deletion separately from the normal deployment workflow:

```bash
nebius-cxcli destroy "$CONFIG" --target CLUSTER_ID --dry-run
```

**Deletion is destructive.** Execution deletes the cluster, dedicated owned GPU
clusters, and owned PVC disks by default. SFS and VM-NFS are preserved by
default. `--preserve-pvc-disks` retains owned PVC disks; `--delete-sfs` requires
verified dedicated storage and rejects deletion-protected SFS. Inspect the
inventory before confirming or supplying `--yes`.

For projects without MK8s, root `destroy` uses rendered-resource teardown.
See [destruction](docs/mk8s.md#destruction) before either form.

### Soperator

| Command | Input | Purpose |
| --- | --- | --- |
| `soperator create` | Deployments path | Configure a fresh Soperator cluster |
| `soperator discover` | Output path and cloud scope flags | Inspect an existing cluster into a support bundle |
| `soperator onboard` | Config path or deployments path | Register an existing official Soperator installation |
| `soperator status` | `CONFIG_YAML` | Show operation state and live health; `--no-live` uses recorded state |
| `soperator upgrade` | `CONFIG_YAML` | Plan or execute an integrated Soperator/Kubernetes/OS/GPU upgrade |
| `soperator profiling install` | `CONFIG_YAML` | Install profiling tools/viewers and reconcile automatic course report delivery |
| `soperator profiling show` | `CONFIG_YAML` | Print access commands for deployed Nsight viewers |
| `soperator profiling recover` | `CONFIG_YAML` and failed-job selectors | Preview or perform supported profiling-job recovery |

Deployments and output paths are directories; a config path identifies the
project's `config.yaml` file.

Create configuration, then follow the shared quick-start deployment sequence:

```bash
nebius-cxcli soperator create ./deployments
```

For an existing installation, onboard it instead of creating a replacement:

```bash
nebius-cxcli soperator onboard "$CONFIG"
nebius-cxcli soperator status "$CONFIG" --target TARGET_ID
```

Onboarding verifies the installed official release and records cluster identity;
it does not select an upgrade or compute/storage migration.

A full-stack upgrade preview uses explicit selectors:

```bash
nebius-cxcli soperator upgrade "$CONFIG" --target TARGET_ID \
  --to-release latest --to-k8s-version latest \
  --to-os auto --to-gpu-stack-preset auto --dry-run
```

To execute, review the plan and rerun without `--dry-run`. Admission freezes the
resolved official release and ordered infrastructure stages. The campaign keeps
scheduling in maintenance through the required product checks, then restores
the exact Slurm state owned by the operation. Default job handling requeues and
holds eligible jobs without cancelling them.

**Jail upgrades replace unprotected content with the target image.** Mandatory
and explicitly admitted protected directories retain their storage. Review
[protected paths and storage](docs/soperator.md#protected-paths-and-storage)
before execution.

For managed Soperator capacity/topology changes, edit configuration, render, and
deploy. Hardware replacements can retire and recreate affected groups; review
the deployment preview. Use `migrate node-group` for a dedicated named
replacement-group migration. These managed topology operations do not apply to
onboarded clusters. Unattended `deploy` defaults to `wait-then-cancel` after `1h`,
unlike upgrade's default; choose job controls explicitly before automation.

Recover interrupted upgrades through `deploy` with the original generation and
options; repeating `soperator upgrade` selects new intent. Status identifies the
recovery entry point but cannot reconstruct all original options.

```bash
nebius-cxcli soperator status "$CONFIG" --target TARGET_ID --show-checks
nebius-cxcli soperator profiling show "$CONFIG" --target TARGET_ID
```

Observability verification is explicit (`soperator status --verify-observability`)
and separate from lifecycle completion. See the [Soperator guide](docs/soperator.md)
for discovery, profiles, job controls, recovery and diagrams, and the
[Nsight guide](docs/nsight-profiling.md) for profiling installation and recovery.
Profiling installation preserves existing application ownership, including
namespaces shared with Soperator, and refuses unrelated application changes.

### Helm-chart (apps) upgrade

| Command | Input | Purpose |
| --- | --- | --- |
| `upgrade helm-chart` | `CONFIG_YAML`, app selector, and `--to-version` | Upgrade an ordinary app chart |

For ordinary chart upgrades use `upgrade helm-chart`; Soperator release changes
belong to `soperator upgrade`.

### Grafana

| Command | Input | Purpose |
| --- | --- | --- |
| `grafana install` | `--config CONFIG_YAML --target TARGET_ID` | Configure Grafana/telemetry, render, and deploy |
| `grafana show` | `--config CONFIG_YAML --target TARGET_ID` | Print access instructions for deployed Grafana |
| `grafana import` | Dashboard path and `--config CONFIG_YAML` | Install dashboards and save project intent |
| `grafana export` | `--url URL` or `--config CONFIG_YAML` | Export dashboard JSON |
| `grafana validate` | Dashboard path or `--config CONFIG_YAML` | Validate standalone JSON or configured dashboards |

```bash
nebius-cxcli grafana install --config "$CONFIG" --target TARGET_ID
nebius-cxcli grafana show --config "$CONFIG" --target TARGET_ID
```

`grafana install` runs the normal deployment workflow, including pending
project changes. Review those changes first. This explicit installation command
defaults telemetry to local storage; supported routing can select local, remote Nebius services, or both.
Collectors and datasources follow that routing and the target type.

`grafana import` changes the live Grafana instance and saves configuration;
`--attach` additionally registers reusable catalog entries. Use the
[dashboard guide](docs/grafana-dashboards.md) for import/export examples,
datasource mapping, overwrite rules, and ownership.

Deployment uses the dashboard assets frozen by `render`, validating their UID
and content hash without reopening the authored files. Missing or changed frozen
assets stop deployment; render again from valid project inputs before retrying.

Repeated imports avoid redundant conversion of already-canonical dashboards;
cached Kubernetes authentication avoids loading the full CLI for each request.
Cache misses retain the existing credential exchange and refresh behavior.
Ownership checks, version guards, recovery copies and readback still run.

See [observability](docs/observability.md) for routing, authentication, and private
browser access.

### VM access

| Command | Input | Purpose |
| --- | --- | --- |
| `ssh-jumphost` | One mode flag taking `CONFIG_YAML` | List/add/remove allowed SSH source CIDRs |
| `wireguard` | One mode flag taking `CONFIG_YAML` | Generate/refresh clients, reconcile networking, verify private access, change future routes, or manage SSH access |

Deploy checks private Kubernetes access when it can identify a connected local
WireGuard client. Missing prerequisites produce a separate `PENDING` result; an
exercised path failure returns nonzero while retaining infrastructure results.
See [MTU repair and client verification](docs/wireguard.md#mtu-repair-and-client-verification).

A new gateway can deploy with no bootstrap clients. First boot records an empty
peer registry, and client issuance remains a separate day-2 operation.

WireGuard uses team-owned SSH credentials and an authenticated connection record
in SecretStash. The wizard writes a linked metadata-only `mysterybox` component
and `access.secretstash_ref` into `config.yaml`. Terraform provisions its three
containers, dedicated VM service account and narrow grants in `generated/infra-access`;
the SDK owns secret versions and rotation. Select an existing administrator group from live
Nebius SDK choices. Administrators use their own Nebius identity to retrieve the
shared login credential temporarily; client generation needs only the source
configuration and its published references. No Terraform/Kubernetes access,
personal SSH key, external-secrets installation or deployer laptop is required.
The installer must also be allowed to create grants on the selected IAM group;
project-scoped provisioning access does not establish that authority for a tenant group.
No group name is fixed. Any selected member can issue configurations; group
members are trusted gateway operators with sudo access. Deployment reports
direct broad grants without revoking them. Quota checks remain read-only, and
credential/IAM preparation runs explicitly before the final deployment plan.
Secret publication preserves returned metadata and verifies the primary version;
the shared journal serializes cxcli writers when SecretStash omits a resource version.
Initial publication accepts an unset primary version; retries reuse recorded
versions and still reject a conflicting primary.

NAT retains its protected local host identity and authenticated Compute recovery.
SSH jump hosts require independently verified trust. All gateway SSH connections
use strict host checking without falling back to machine-global trust.
WireGuard additionally isolates local SSH configuration
and agent identities. Its optional known-hosts file must agree with SecretStash.
See [WireGuard access](docs/wireguard.md) for setup, rotation and recovery.

External Secrets Operator is selected independently by the user. Selecting ESO
automatically configures eligible application-secret synchronization on its target
unless `secrets.mysterybox.enabled: false` is set. Selecting MysteryBox does not
select ESO. WireGuard access containers never enter Kubernetes synchronization.
The MysteryBox wizard asks for Kubernetes Secret names and ESO version policies
only when synchronization is enabled on an eligible target. Otherwise it collects
only SecretStash secret names and payload keys/types, without adding sync metadata.

Ordinary MysteryBox configuration starts with
`Sync SecretBox secrets to Kubernetes secrets using ESO? [n]:`.
The default is No when sync is inactive, or Yes when already enabled. With several
Kubernetes targets, choose which ones to configure first; other targets keep their
settings. Each choice applies to all ordinary MysteryBox secrets on that target.
Yes explicitly offers to add or enable ESO if needed, then asks for synchronization
namespaces. No persists the target's sync opt-out while preserving its ESO chart and
existing secret metadata. Without a Kubernetes target, choose No for standalone
secrets. WireGuard's managed access containers never show this setup.

```bash
nebius-cxcli ssh-jumphost --list-allowed-cidrs "$CONFIG" \
  --component ssh-jumphost@bastion --ssh-known-hosts-file ./known_hosts
nebius-cxcli wireguard --gen-client-conf "$CONFIG" \
  --component wireguard-gw@vpn --client-name laptop
```

Use one WireGuard configuration per device. Downloaded `.conf` files contain
private keys and must not enter Git. Route changes affect future client files;
existing distributed files are not automatically rewritten. See
[VM access](docs/project-workflows.md#vm-access) for selectors and day-2 modes.

### Authentication, CI, and reporting

| Command | Input | Purpose |
| --- | --- | --- |
| `auth` | Mode/scope flags | Ensure or validate project credentials; explicitly rotate or sync them when requested |
| `bootstrap-ci` | `CONFIG_YAML` | Reconcile deployment workflow and GitHub environment credentials |
| `email` | `CONFIG_YAML` or `--setup` | Send the existing deployment report or configure SMTP |

```bash
nebius-cxcli auth --validate-profile
nebius-cxcli bootstrap-ci "$CONFIG"
```

Normal project commands ensure runtime credentials automatically. `auth --recreate`
explicitly rotates a project's authorized key. CI bootstrap changes workflow
files and GitHub environment secrets; it requires an authorized GitHub token and
the intended repository. The generated workflow validates, renders, validates
the bundle, and deploys.

`email` sends a message; it is not a report preview. It uses the existing rendered
report and recipient/runtime settings in the generated manifest. See
[CI and reporting](docs/project-workflows.md#ci-and-reporting).

<!-- command-index:end -->

## Security and operational essentials

NAT, WireGuard, and Tailscale HTTPS probes require TLS 1.2 or newer and verify
server certificates and hostnames.

- Keep deployment repositories private. Version `config.yaml` and deployable
  generated artifacts, while respecting generated runtime-file ignores.
- Keep credentials, raw Kubernetes Secrets, WireGuard private keys, kubeconfigs,
  customer data, and private endpoints out of public documentation and Git.
  Store task-required secrets only in the intended runtime secret store.
- `generated/infra/terraform.auto.tfvars.json` is sensitive runtime output. Keep
  it ignored; cxcli reconstructs it from the generated manifest when needed.
- Inspect infrastructure and app diffs before applying changes. Resource
  renames, component removals, and replacement inputs can delete live resources.
- Serialize mutations for one backend across operators and CI. Do not bypass
  an active Terraform lock or rewrite recovery evidence.
- Use explicit target selection and verified identity. Do not rely on the
  workstation's current Kubernetes context to select a deployment.

## Detailed guides

| Guide | Content |
| --- | --- |
| [Configuration reference](docs/configuration-reference.md) | Catalogs, schemas, bindings, source profiles, defaults, and secrets configuration |
| [Project workflows](docs/project-workflows.md) | Authentication, project changes, deploy/recovery, Terraform/Flux, VM access, CI, and reporting |
| [MK8s](docs/mk8s.md) | Software upgrades, hardware migration, acceptance suites, and deletion |
| [Soperator](docs/soperator.md) | Create, discover, onboard, status, upgrades, protected storage, and recovery |
| [Observability](docs/observability.md) | Local/remote routing, Grafana installation, collectors, and access |
| [Grafana dashboards](docs/grafana-dashboards.md) | Dashboard import/export, datasource mapping, and ownership |
| [Nsight profiling](docs/nsight-profiling.md) | Profiling installation, viewers, and worker-tool recovery |
| [Compatibility](docs/compatibility-matrix.md) | Admission checks, supported interfaces, and compatibility maintenance |

## Development

Contributors should use the locked Make/uv workflow in the
[development guide](docs/development.md), which also covers focused tests,
quality gates, wheel verification, and releases. Product contracts live in
[requirements](docs/requirements.md) and [design](docs/design.md); release history
is in [CHANGELOG.md](CHANGELOG.md).

Intentional command or CLI help changes require a reviewed fixture refresh;
see the [CLI contract workflow](docs/development.md#quality-gates).

CI retains its Python 3.12–3.14 matrix and one-build/many-consumer wheel checks.
Its dedicated NAT probe integration step requires Docker and verifies the private
SSH collector with two isolated Linux SSH servers, without cloud credentials.
The fixture stages its known-hosts data in an owner-only container runtime file
so host bind-mount UIDs do not invalidate strict SSH trust checks.
Merge-identity and result-evidence jobs also verify authenticated post-merge runs.
Workflow tests require exactly one setup-uv action pinned to a full commit SHA,
while preserving uv version, cache inputs, conditions and setup ordering.
Valid immutable action updates do not require a duplicated test hash change.
File-preservation tests check content and write metadata while allowing reads to
update access times.
Source-contract tests use `tests/source_inspection.py` to locate current function
definitions when edits shift imported line numbers. Rerun tests after edits to
validate the final checkout.
