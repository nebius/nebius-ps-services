# Synthetic design and regional evidence

Everything here is a fixture, not provider availability or live cloud state.
Environment: Dev/Test. Deploy a CPU API and self-hosted database on Nebius MK8s,
plus a GPU worker, persistent storage, Object Storage backups and one public IP.
Locality requires region-a. Inventory operational dependencies; do not change
the database to a managed service. No project has been selected.

CPU pools add two 16-vCPU nodes and one replacement node: steady demand 32 vCPU,
maximum incremental demand 48 vCPU. All current tenant usage is unrelated and
will remain allocated. GPU demand is one VM of preset gpu-small on platform
example-gpu; gpu-large is an alternative shape, not another simultaneous pool.
Storage: 300 GiB persistent disk and 200 GiB object backups. No storage/network
quota or verified price evidence is supplied.

The synthetic official regional catalog lists public region-a and region-b.
Both support the chosen Compute/MK8s/storage services and candidate presets.
Region-b remains excluded by the design locality constraint. Tenant quota
snapshots are complete and all usage is active/measured:

| Region | CPU limit | Current usage |
| --- | --- | --- |
| region-a | 64 | 24 |
| region-b | 128 | 32 |

GPU advice was collected for tenant-example. These rows refer to the same
underlying fabric; no additive capacity or joint-placement proof is supplied.

| Region / fabric / preset | Allocation type | Available VMs | Data state | Measurement time |
| --- | --- | --- | --- | --- |
| region-a / fabric-a / gpu-small | on_demand | 4 | FRESH | 2026-10-26T10:00:00Z |
| region-a / fabric-a / gpu-large | on_demand | 2 | FRESH | 2026-10-26T10:00:00Z |
| region-a / fabric-a / gpu-small | reserved | 0 | STALE | 2026-10-25T10:00:00Z |
| region-a / fabric-a / gpu-small | preemptible | 0 | FRESH | 2026-10-26T10:00:00Z |

No selected human identity is currently available. No CPU physical capacity,
project platform eligibility or project limits have been verified.
