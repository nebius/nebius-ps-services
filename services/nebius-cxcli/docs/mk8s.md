# MK8s operations

[Back to MK8s commands](../README.md#mk8s)

Examples assume a rendered project, an installed CLI, and verified target
identity. Set `CONFIG` to the project file and replace `TARGET_ID` with its
logical cluster instance ID. Versions, hardware presets, fabrics, and capacity
must be available for that target; example values are not compatibility claims.

## Private-cluster deployment

For a managed CPU-only target with no enabled charts or in-cluster integrations,
`deploy` uses the Nebius SDK to verify infrastructure after Terraform. This also
works when `inputs.cluster.public_endpoint` is `false` and the runner cannot
reach the private Kubernetes endpoint. The required smoke report is labeled
**MK8s infrastructure readiness (Nebius API)**.

Acceptance checks the Terraform-owned cluster ID, project and name, endpoint
presence, settled cluster state, complete node-group inventory, desired capacity,
ready nodes and absence of outdated nodes. API failures, missing groups and
readiness timeouts fail deployment. Final verification reads the provider again.
The report records each executed cluster, endpoint, inventory and node-group
check. Deployment acceptance uses the same saved report as the final summary;
missing or skipped required checks fail deployment.

This verifies cloud infrastructure only. It does not inspect existing workloads,
Helm releases, pod health or application ownership on a new runner. Known local
application bindings or completed application evidence retain Kubernetes checks;
removing charts from configuration does not uninstall them. Infrastructure-only
acceptance never creates a Kubernetes UID binding or claims workload validation.

App-bearing, GPU and external targets still require Kubernetes access. Use a
runner on the private network or an established VPN/routed connection for their
deployment, Flux operations and workload checks. The SDK is not a Kubernetes API
proxy. Infrastructure-only deploy does not activate a VPN or persist kubeconfig.
Keep the frozen generated bundle and rerun the original command after a failed
deployment; do not rerender an interrupted operation just for this repair.

See the official [cluster connection guide](https://docs.nebius.com/kubernetes/connect)
and [node-group status contract](https://docs.nebius.com/terraform-provider/reference/resources/mk8s_v1_node_group#nested-schema-for-status).

## Cluster creation and node placement

Run `nebius-cxcli component add infra:mk8s --config "$CONFIG"` to configure a
cluster and its node groups. After each new group's name and size, the wizard
offers a placement rule:

| Choice | Saved node-template fields | Workload requirement |
| --- | --- | --- |
| None (default) | No custom labels or taints | No custom placement rule |
| Label | `node_labels` | Use matching `nodeSelector` entries to require these nodes |
| Taint | `taints` | Matching tolerations permit scheduling but do not select these nodes |
| Both | `node_labels` and `taints` | Use matching selectors and tolerations |

Enter explicit keys and values; values default to the node-group name. In Both
mode, the first taint defaults to the first label's key/value. You can add
multiple labels and taints. Duplicate label keys or taint key/effect pairs are
rejected. Going back or quitting during placement discards the incomplete new
group and preserves completed groups. The renderer still supplies its automatic
node-group and GPU labels.

Choose `NoSchedule` (default), `PreferNoSchedule`, or `NoExecute` per taint.
NoSchedule blocks new Pods without a matching toleration; PreferNoSchedule is
a soft preference; NoExecute can also evict existing Pods lacking a matching
toleration. The config uses Nebius enum names as shown below.

For example, this fragment belongs under the MK8s component's `inputs` beside
`cluster`. The hardware values must be valid for your project:

```yaml
node_groups:
  core-services:
    node_count: 6
    platform: cpu-d3
    preset: 4vcpu-16gb
    node_labels:
      scheduling.example.com/pool: core
  task-runners:
    node_count: 4
    platform: cpu-d3
    preset: 4vcpu-16gb
    node_labels:
      scheduling.example.com/pool: task
    taints:
      - key: scheduling.example.com/pool
        value: task
        effect: NO_SCHEDULE
```

These native fields flow through render/deploy into the node-group template.
There is no separate persisted placement-rule field. Authored configuration is
validated for Kubernetes key/value syntax and provider-supported effects.
Nebius ignores node label keys containing `kubernetes.io` or `k8s.io`, so cxcli
rejects them. Node-template label/taint changes apply to newly created nodes;
they do not relabel existing nodes or automatically roll a node group.
See the [Nebius node-group resource reference](https://docs.nebius.com/terraform-provider/reference/resources/mk8s_v1_node_group).

The wizard prints guidance only. For core-service Pods, put this in the Pod
spec (or the workload controller's `spec.template.spec`):

```yaml
spec:
  nodeSelector:
    scheduling.example.com/pool: core
```

For task-runner Pods:

```yaml
spec:
  nodeSelector:
    scheduling.example.com/pool: task
  tolerations:
    - key: scheduling.example.com/pool
      operator: Equal
      value: task
      effect: NoSchedule
```

Cluster deployment does not edit application manifests. Labels alone do not
force applications onto a group; tolerations alone do not direct them to it.
See [Kubernetes taints and tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/).

### Shared storage for new node groups

Ordinary `create` and `component add` configure selected VPC components first,
then selected SFS components, then the remaining infrastructure. This lets you
finish naming shared filesystems before choosing attachments for new node groups.
Existing SFS configuration is also available when adding a cluster.

For each new group, the wizard shows **Attach shared storage to node group
"core-services":** and lists eligible filesystems by their configured names. All start
checked. Use Space to toggle individual filesystems and Enter to confirm; you
can uncheck all to attach none. The text prompt accepts comma-separated indexes,
Enter for all, or `none`. Each group has an independent selection, and the
confirmation shows the selected names or `none`. No eligible filesystem means
no attachment prompt. Existing groups and Soperator's role-based storage flow
are unchanged.

The saved `sfs_filesystem_keys` list still contains internal references: for a
single-filesystem SFS component, `default` identifies that filesystem. An empty
list records an explicit opt-out. These references are resolved to filesystem
IDs and mount tags during rendering; they are not access keys or PVC names.

The wizard configures worker-node attachments for deployment. Node mounting,
the CSI driver, and Kubernetes PV/PVC resources are separate setup steps;
selecting storage here does not provision or mount it immediately. See the
[Nebius shared filesystem CSI guide](https://docs.nebius.com/kubernetes/storage/filesystem-over-csi).

### Existing and planned VPC choices

A configured VPC subnet confirmed by exact managed state and live cloud identity
appears once with its cloud ID and an `existing; managed by ...` annotation.
Selecting it retains its component binding in `config.yaml`. A genuinely new
configured subnet appears as `planned:`. Names and CIDRs can detect possible
collisions but cannot establish ownership.

The wizard reads the frozen generated manifest's exact state object using
available credentials, without initializing Terraform or creating credentials.
Missing access, inconsistent state, missing live resources, or incomplete
discovery produce a diagnostic and block automatic selection of affected
bindings. Malformed module inventory is unresolved, and a choice filter that
hides live resources cannot establish that a configured resource is new.
Unrelated live resources remain manually selectable. Resolve the
reported state/discovery problem before selecting the managed binding.

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
