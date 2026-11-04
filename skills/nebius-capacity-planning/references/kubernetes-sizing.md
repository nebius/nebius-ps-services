# Kubernetes sizing

## Version and evidence

Resolve the target Nebius Kubernetes version before using version-dependent
features. Read that version's official docs and observed admission policies.
Do not assume the newest upstream feature is enabled by Nebius.

Use container-level recommendations by default. If an existing design uses
Pod-level resources, resize features or other version-dependent mechanisms,
account for their effective semantics explicitly instead of silently summing
containers. Mark unsupported arithmetic unresolved.

## Requests, limits and measurements

- Set CPU requests from representative demand at the required throughput and
  latency, including concurrency and startup/recovery needs. Record observation
  windows and percentiles; neither instantaneous `kubectl top` nor one universal
  percentile is a sufficient sizing model.
- Set memory requests from the workload's defensible resident/working-set
  budget, including non-heap, caches, sidecars and memory-backed volumes.
  Working-set metrics alone can hide important peaks; check OOM history,
  application memory behavior, restart and compaction/recovery.
- Set memory limits above the justified peak envelope with an explicit margin.
  Keep application heaps/caches below that limit to leave space for native
  allocations, buffers and runtime overhead. Explain the scheduling risk when
  requests are substantially below expected simultaneous memory usage.
- CPU limits are workload-specific: finite caps can suit bounded batch or
  untrusted work; latency-sensitive services may warrant an unset CPU limit.
  Always explain unset values and assess contention/noisy neighbors, namespace
  policies and admission defaults. Never turn "unset" into zero.
- For container-level Guaranteed QoS, all relevant app/init containers need
  CPU request equal to CPU limit and memory request equal to memory limit.
  Do not claim Guaranteed QoS when CPU limits are unset, or equate it with
  availability/performance guarantees.

CPU limits throttle; memory enforcement may terminate containers under memory
pressure. Requests affect scheduling. A limit supplied without a request can
become the request through defaulting. Inspect LimitRange, ResourceQuota and
other admission policies when available; do not change them.

## Effective Pod budgets

For conventional init containers, compare the largest init-stage requirement
with the sum of concurrently running application containers. Add Pod overhead
once, when configured.

For native sidecars (`initContainers` with `restartPolicy: Always`), build a
stage ledger in declaration order. Keep every started sidecar in the demand
of subsequent init stages; in steady state add all sidecars to app containers.
For each resource, take the maximum concurrent stage demand, then add Pod
overhead once. A simple maximum of individual init containers undercounts
overlapping sidecars. Apply target-version limit/defaulting rules separately.

Feed the calculator those **already derived effective Pod budgets**, not raw
container specs. Preserve the stage ledger in report calculation notes.
It intentionally does not parse manifests or infer Kubernetes semantics.
Use whole GPUs consistent with the target device plugin/preset. Do not invent
fractional GPU sharing or memory pooling; qualify any explicitly configured
sharing mechanism.

Budget disk-backed `emptyDir`, writable layers and logs as ephemeral storage.
Memory-backed `emptyDir` belongs in the memory budget, with a justified size
limit recommendation. Avoid charging the same bytes to both resources.

## Nodes and placement

Prefer observed `.status.allocatable`. This already accounts for node
reservations: do not subtract `kubeReserved` again. For a new cluster, use
current Nebius reservation guidance plus explicit assumptions for other
system/eviction overhead. Compute observed allocatable separately from
planning allowances.

Subtract infrastructure Pod requests once from allocatable: CNI, CSI,
telemetry agents, node-local DNS, GPU plugins and other DaemonSets. Include
their limits/runtime demand and Pod slots. Controllers and CoreDNS that are
Deployments are counted by replica, not automatically once per node.

For every pool and required scenario check:

- Effective Pod request fits an eligible node in every dimension.
- A feasible distribution exists given affinity/anti-affinity, taints,
  topology, volume attachment/mount constraints and Pod limits.
- CPU contention and simultaneous memory/ephemeral demand are acceptable,
  including failure-induced concentration and startup overlap.
- Node minimum/maximum and boot disks, subnet addresses and quota support the
  scenario. An autoscaler maximum is not provisioned spare capacity.

The offline calculator supplies necessary arithmetic checks only. Show the
placement reasoning separately; a PDB neither creates spare capacity nor
guarantees survival of involuntary node failure.

## Autoscaling, jobs and rollout

Record replicas/concurrency at minimum, desired and maximum. CPU-utilization
HPA targets are relative to CPU requests; changing requests changes scaling
behavior. Missing requests can make utilization undefined. Prefer workload
signals such as queue depth when CPU is a poor demand proxy. Check metrics
availability, startup delay and scale-up lag.

Existing VPA recommendations can inform sizing. VPA is an optional separately
installed controller; recommendation-only `Off` mode does not apply changes.
Do not install/enable it as part of assessment. Review interactions before
recommending concurrent HPA/VPA control of the same resource signal.

For application rollout, account for actual strategy, percentage rounding,
maxSurge and terminating Pod overlap; old Pods can still consume resources
after they stop counting as available replicas. For node upgrades, separately
count additional provisioned nodes, draining nodes and replacement lag.
Include Job/CronJob overlap, retries, parent/child worker dependencies and
backfill limits. Avoid admission caps that deadlock parent tasks waiting for
children in the same full worker pool.

## Official sources

Reviewed 2026-10-20; recheck the target version at assessment time.

- [Resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
- [Sidecar resource sharing](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/)
- [Node allocatable](https://kubernetes.io/docs/tasks/administer-cluster/reserve-compute-resources/)
- [QoS classes](https://kubernetes.io/docs/tasks/configure-pod-container/quality-service-pod/)
- [Horizontal autoscaling](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
- [Vertical autoscaling](https://kubernetes.io/docs/concepts/workloads/autoscaling/vertical-pod-autoscale/)
- [Deployment rollout](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
- [Disruptions and PDBs](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/)
- [Nebius kubeReserved](https://docs.nebius.com/kubernetes/node-groups/kube-reserved)
