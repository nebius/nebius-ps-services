# MK8s operations

[Back to MK8s commands](../README.md#mk8s)

Examples assume a rendered project, an installed CLI, and verified target
identity. Set `CONFIG` to the project file and replace `TARGET_ID` with its
logical cluster instance ID. Versions, hardware presets, fabrics, and capacity
must be available for that target; example values are not compatibility claims.

## Choose the operation

| Change | Command |
| --- | --- |
| Kubernetes, node OS, Nebius-image GPU stack | `upgrade node-template` |
| Permanent named replacement for hardware, preset, reservation, GPU-cluster, or fabric changes | `migrate node-group` |
| Coordinated Soperator release/Kubernetes/OS/GPU campaign | `soperator upgrade` |
| Ordinary app chart version | `upgrade helm-chart` |
| Whole-cluster deletion | `destroy --target CLUSTER_ID` |

Managed Soperator config reconciliation also supports capacity/topology changes
through render/deploy, including retiring and recreating changed groups. Review
that preview carefully. It differs from a dedicated named replacement-group
migration and is not supported for onboarded topology. See [Soperator](soperator.md).

## Node-template upgrades

The guided command discovers compatible live choices:

```bash
nebius-cxcli upgrade node-template "$CONFIG"
```

A focused preview can select a target and node group explicitly:

```bash
nebius-cxcli upgrade node-template "$CONFIG" infra:mk8s@TARGET_ID \
  --node-group system --to-os ubuntu24.04 --dry-run
```

For a combined software upgrade:

```bash
nebius-cxcli upgrade node-template "$CONFIG" infra:mk8s@TARGET_ID \
  --to-version 1.33 --to-os ubuntu24.04 \
  --to-gpu-stack-preset cuda13.0 --dry-run
```

Omitted Kubernetes version keeps the current control-plane minor. Omitted OS
and GPU stack keep the selected live value when unambiguous and compatible.
The command advances the control plane when required, then combines selected
node-group software changes so each group rolls once. It does not change
hardware platform, preset, CPU/GPU kind, GPU-cluster, or fabric.

Review the preview, then rerun without `--dry-run` to execute. Automation uses
`--no-interactive` with explicit selections. Non-dry runs finish with live
control-plane and selected-group readiness and write
`generated/reports/upgrade-node-template-report.{md,json}`.

### Rollout strategies

| Strategy | Surge / unavailable defaults | Auto drain timeout | Tradeoff |
| --- | --- | --- | --- |
| `zero-surge` (default) | 0 / 1 | 30m | No spare-node quota; temporary capacity reduction |
| `safe-surge` | 1 / 0 | 30m | Preserves active capacity using spare quota/capacity |
| `force-delete` | Explicit provider drain fallback | 10m | Last resort; forced Pod deletion risks workload consistency |

Use `--strategy` to select policy. For safe surge,
`--strategy-max-surge-count N` requests a positive temporary-node count per
active group. GPU-cluster spare capacity must match the existing fabric and
reservation policy; another fabric is a migration, not spare local capacity.

`--drain-timeout auto` uses the values above. Explicit durations use Go-style
units such as `30m` or `1h`; `none` waits indefinitely instead of enabling a
finite provider drain fallback. This differs from cxcli's whole-group rollout
watch, which uses `max(1h, 10m * target node count)` after each Terraform apply.

Preflight inspection failures block execution even with force-delete. Pod
Disruption Budget blockers remain meaningful. Node-template upgrade does not
delete PVC/PV objects, but forced Pod deletion can still affect applications
using shared storage, locks, or external services. Temporary strategy settings
are restored after the staged operation; interrupted rollout can require a
final apply to restore the configured strategy.

## Permanent node-group migration

Migration creates a new configuration key and provider name, proves replacement
readiness, cuts placement over, retires the source group, and restores
appropriate autoscaling. Supply the source and replacement identities explicitly:

```bash
nebius-cxcli migrate node-group "$CONFIG" infra:mk8s@TARGET_ID \
  --node-group worker \
  --replacement-node-group worker-b200 --replacement-name worker-b200 \
  --to-platform gpu-b200-sxm --to-preset 8gpu-160vcpu-1792gb --dry-run
```

Use live-valid hardware values and only changed target fields. A same-hardware
request is not a hardware migration. Inspect the report and capacity/storage
preflight before execution; automation must supply `--execute --approve` with
its complete reviewed selection.

Migration has its own receipt, recovery ledger, generated-config chain, and
reports under `generated/reports/migrate-node-group-report.{md,json}`. It is
separate from Soperator upgrade and is not shown by `soperator status`.
Follow its own reported recovery instructions rather than using an upgrade
receipt to resume it.

For Soperator placement, migration protects the frozen source workers, stages
placement and cutover through its own executor, and restores its maintenance
after final readiness. Quota, immutable resource identity, and protected-storage
checks remain prerequisites.

## Acceptance testing

Acceptance commands run explicit post-deploy workloads. They write local JSON
reports without editing config or replacing the deploy report; they do create
cluster workloads. They use deployment handoff, a verified explicit/unambiguous
kubeconfig context, or a known cluster ID rather than initializing Terraform
state. If handoff is missing, complete deploy/apply first.

| Suite | Command | What it tests |
| --- | --- | --- |
| `k8s-cuda` | `acceptance-test smoke` | CUDA workload admission and GPU visibility on scheduler-free Ready GPU nodes |
| `k8s-nccl` | `acceptance-test benchmark` | Temporary MPIJob/NCCL execution and selected bandwidth threshold |

```bash
nebius-cxcli acceptance-test smoke "$CONFIG" \
  --suite k8s-cuda --target TARGET_ID
nebius-cxcli acceptance-test benchmark "$CONFIG" \
  --suite k8s-nccl --target TARGET_ID --max-nodes 2 --timeout 30m
```

Omitting target selection runs generated targets, equivalent to `--all-targets`.
Suites apply only to compatible target shapes. NCCL chooses Socket/TCPIP for
Ethernet-only shapes and RDMA for applicable GPU-cluster/InfiniBand shapes.
For one-GPU shapes, completed NCCL below-threshold average bandwidth is recorded
as a comment rather than treating it as a multi-GPU RDMA qualification result.

Benchmark requires `--suite`. Without run limits, it uses all schedulable GPU
nodes and no cxcli timeout. The default average bus-bandwidth threshold is
300 GB/s; adjust `--average-bus-bandwidth-threshold-gbps` for the intended test.
Report scope, skips, transport, and eligibility matter when interpreting results.

Reports are `acceptance-smoke-report-TARGET_ID.json` and
`acceptance-benchmark-report-TARGET_ID.json` under `generated/reports/`.
Terminal results show PASSED/FAILED/SKIPPED, scope, target, and elapsed time.
These ad-hoc reports do not substitute for Soperator lifecycle acceptance.

## Destruction

**MK8s destruction permanently deletes the selected cluster and default owned
storage inventory.** It requires the immutable cloud cluster ID, not a logical
target selector, even when only one cluster is configured.

```bash
nebius-cxcli destroy "$CONFIG" --target CLUSTER_ID --dry-run
```

The preview writes a local receipt and shows the verified deletion inventory.
Review it, then execute by omitting `--dry-run` and confirming. `--yes` replaces
interactive confirmation; it does not bypass inventory or ownership checks.
Managed and onboarded clusters use the same cloud-API path without Kubernetes
API access. Managed resources also receive constrained state reconciliation.

| Resource | Default and options |
| --- | --- |
| Selected cluster and node groups | Deleted |
| Verified dedicated owned GPU clusters | Deleted |
| Owned PVC disks | Deleted permanently; `--preserve-pvc-disks` retains them |
| Attached SFS | Preserved; `--delete-sfs` requires verified dedicated, unprotected storage |
| VM-NFS and unrelated infrastructure | Preserved |

`forbid_deletion` blocks SFS deletion. Unknown/shared/protected storage is not
made eligible by `--yes`. Missing or ambiguous identities stop execution;
resource names alone are insufficient. Follow the command's receipt/recovery
instructions after interruption, preserving the approved inventory.

For a project with **no MK8s**, root destroy instead tears down rendered resources:

```bash
nebius-cxcli destroy "$CONFIG"
```

The generated-bundle `terraform destroy` path is also restricted to eligible
non-MK8s projects. Do not use raw Terraform teardown to bypass MK8s retirement.
