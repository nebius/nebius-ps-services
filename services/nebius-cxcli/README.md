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

Use Git, an installed Python **3.12, 3.13, or 3.14**, and uv **0.12.9 or a
compatible 0.12.x release**. Install the CLI with `uv tool` to run
`nebius-cxcli` from any directory without activating a virtual environment.

If uv is not installed, install it with an existing, working `pipx`:

```bash
pipx install 'uv>=0.12.9,<0.13'
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
| SSH access and a verified known-hosts file | WireGuard and SSH jump-host day-2 operations |

Managed tool downloads need access to official HashiCorp or GitHub releases
and verify published checksums. Keep `kubectl` within the target server's
[supported version skew](https://kubernetes.io/releases/version-skew-policy/).
For a private Kubernetes endpoint, establish the network path before running
commands that connect to it. **MK8s `destroy --target CLUSTER_ID` uses cloud APIs
and does not need Kubernetes API access.**

A standalone `nebius` CLI is not required for ordinary operation; cxcli uses the
Nebius Python SDK. Automatic quota-request submission is a separate internal
operator capability described below.

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
| `component add` | Selectors and `--config CONFIG_YAML` | Add infrastructure or target-bound apps |
| `component remove` | Selectors and `--config CONFIG_YAML` | Remove component selections from configuration |
| `validate` | `CONFIG_YAML` | Check configuration, readiness, networking, and live quota/capacity |
| `validate-generated` | `GENERATED_PATH` | Validate the rendered bundle |
| `validate-sources` | Optional source-catalog path | Validate Terraform/Helm sources and catalog wiring |
| `render` | `CONFIG_YAML` | Generate the deployment bundle |

```bash
nebius-cxcli component list --config "$CONFIG"
nebius-cxcli component add infra:vm --config "$CONFIG"
nebius-cxcli validate "$CONFIG"
nebius-cxcli render "$CONFIG" --force
```

Review removals and the generated diff before applying them. `render --force`
approves replacement of local generated artifacts, not a live-resource plan.

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
| `flux apply` | `GENERATED_PATH` | Apply apps directly and wait for readiness |
| `flux bootstrap` | `GENERATED_PATH` | Establish or reconcile continuous GitOps delivery |
| `flux destroy` | `GENERATED_PATH` | Remove eligible rendered app resources |

```bash
nebius-cxcli terraform plan "$GENERATED"
nebius-cxcli deploy "$CONFIG"
```

Use `deploy` for initial Soperator installation and protected changes. Dedicated
Terraform mutations and Flux bootstrap/destroy reject Soperator targets.
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
| `soperator profiling install` | `CONFIG_YAML` | Install the profiling stack and worker tooling |
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
project changes. Review those changes first. Telemetry defaults to local
storage; supported routing can select local, remote Nebius services, or both.
Collectors and datasources follow that routing and the target type.

`grafana import` changes the live Grafana instance and saves configuration;
`--attach` additionally registers reusable catalog entries. Use the
[dashboard guide](docs/grafana-dashboards.md) for import/export examples,
datasource mapping, overwrite rules, and ownership.

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
| `wireguard` | One mode flag taking `CONFIG_YAML` | Generate a client configuration or change future client routes |

Both commands require a deployed matching component and SSH authentication.
Supply an independently verified host key in `generated/ssh_known_hosts`, or
use `--ssh-known-hosts-file` for a file outside generated artifacts. There is no
first-seen-key acceptance or machine-global known-hosts fallback. Rendering
replaces `generated/`, so restore that verified file afterward if using the
default location.

```bash
nebius-cxcli ssh-jumphost --list-allowed-cidrs "$CONFIG" \
  --component ssh-jumphost@bastion --ssh-known-hosts-file ./known_hosts
nebius-cxcli wireguard --gen-client-conf "$CONFIG" \
  --component wireguard-gw@vpn --client-name laptop \
  --ssh-known-hosts-file ./known_hosts
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

CI retains its Python 3.12–3.14 matrix and one-build/many-consumer wheel checks.
Merge-identity and result-evidence jobs also verify authenticated post-merge runs.
File-preservation tests check content and write metadata while allowing reads to
update access times.
