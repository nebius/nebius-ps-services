# Sizing methodology

## Evidence and uncertainty

Read the design before selecting SKUs. Build a coverage ledger linking each
component ID to its design section, deployment unit and dependencies. Shared
services have one owner row; external or excluded components need a disposition.
Do not omit operators, DNS, CNI/CSI, ingress, certificates, telemetry, backups,
scheduled work or in-cluster build/test agents when the selected design uses them.

For resource quantities prefer representative measured load and benchmarks,
then component-version vendor guidance, then a transparent engineering estimate.
Manifests show configured budgets, not measured needs. Tutorial values are not
vendor minima. Record source, version, date, workload, statistic/window and
confidence. Current official provider docs/API establish capabilities and
supported shapes, not application throughput.

When there is no runtime, give a useful initial numeric budget where defensible,
label it provisional and explain what would change it. Ask about inputs whose
uncertainty changes the architecture, cost tier or availability promise. Show
low/base/high sensitivity for unresolved load rather than false precision.

## Compute and headroom

Separate four ledgers:

1. Per-container and effective per-Pod resource requests.
2. Expected simultaneous usage, startup/recovery demand and finite limits.
3. Node provisioned capacity, allocatable capacity and overhead Pods.
4. Cloud billable resources and incremental quota demand.

For homogeneous pools, an aggregate lower bound is the maximum of
`ceil(total demand / per-node workload capacity)` across CPU, memory, GPU,
ephemeral storage and Pod slots. This is only a lower bound: indivisible Pods,
affinity, topology, taints and volume restrictions may require additional nodes.
Do not divide aggregate demand across an incompatible pool.

Headroom must name its purpose: measured variability, growth, recovery,
rollout overlap or provisioning delay. For a spare fraction `h` of usable
capacity, required capacity is `demand / (1-h)`, not `demand * (1+h)`.
Show absolute spare and spare percentage with the denominator. There is no
universal recommended percentage. Use evidence and explicit assumptions.
Do not add a growth factor twice or reserve node loss both through extra nodes
and an unexplained percentage.

Each scenario declares active workloads, concurrency, provisioned nodes,
unavailable nodes, replacement surge and relevant storage/network demand.
Unavailable nodes may still consume quota and cost. Count provisioned surge
even if those nodes are not yet usable. Test combined peak/failure/rollout only
where the objective requires it; otherwise document the operational restriction.
Separate "can schedule requests" from "can serve peak load within the SLO."

For VMs, use process/service budgets plus OS/runtime overhead and the same
failure/growth method. For managed services, use their actual capacity/SKU
model, cluster counts, replicas and limits; do not invent underlying worker
resources or double-count a managed control plane as Kubernetes worker Pods.

## Storage

Create a ledger for each data class and storage resource:

| Input | Assessment |
| --- | --- |
| Logical bytes | Initial data, daily ingestion, retained days and growth horizon |
| Amplification | Indexes, metadata, WAL/journal, compaction and temporary copies |
| Replication | Application copies and ownership; provider-managed redundancy is not an extra customer allocation unless billed that way |
| Recovery | Full/incremental backup cadence, retention, restore workspace and transfer time |
| Performance | Read/write mix, IOPS, operation size, throughput, latency and metadata operations |
| Compatibility | Access mode, sharing, consistency, locks, durability, fencing, attachment and project/region constraints |
| Capacity | Per-volume/PVC/filesystem/bucket budget, number of resources, growth trigger and billable unit |

Use separate live-data, backup and scratch rows. Three application replicas of
a 100 GiB data set consume 300 GiB of allocated live-data budget; one independent
150 GiB backup budget adds 150 GiB, not another three copies. Do not multiply
daily ingestion by retention when the supplied size already includes the
retention window. Declare compression/deduplication as assumptions.

Select capacity sufficient for both bytes and the provider's performance
scaling rules. Performance units are not automatically minimum sizes.
SFS, block and object storage are different interfaces. Never assume an SFS
mount replaces required object versioning/lifecycle/conditional-write behavior,
that PVC requests enforce per-directory byte quotas, or that separate
filesystems establish independent physical failure domains. Verify these
properties against current service/CSI documentation.

## Networking and supporting services

Map traffic flows: ingress, egress, service-to-service, replication, storage,
backups, image pulls and remote model APIs. Estimate peak bandwidth, concurrent
connections and packets/operations where relevant, plus monthly transfer volume
separately. Aggregate flows at actual shared bottlenecks; do not count the same
flow twice on a link.

Account for subnet free addresses, per-node Pod CIDRs, Service ranges, peak
nodes and surge, public IPs, load balancers, DNS, egress/NAT and gateways.
Private nodes still need a supported egress path and its capacity budget.
Do not assume a managed HA NAT service or multi-zone topology exists.
Check preset network limits and storage traffic contention. Include
InfiniBand/fabric/topology constraints for applicable GPU workloads.

## Costs and qualification

Use verified region/SKU/unit rates with currency and retrieval date. State
hours per month explicitly; 730 hours is an optional always-on estimate, not
a fact about Dev/Test schedules. Include persistent disks, filesystems, object
storage operations, transfer, IPs/LBs, managed services and telemetry as charged.
Show temporary surge separately. Never infer contracted discounts or treat
an unpriced dependency as free. Identify subtotals and cost coverage.

End with concrete qualification targets: expected load and duration, latency/
throughput objectives, throttling/OOM thresholds, storage latency/IOPS and
restore time, failover behavior, network capacity and growth alerts. Recommend
tests; do not execute them during an assessment.
