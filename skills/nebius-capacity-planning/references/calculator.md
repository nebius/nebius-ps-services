# Offline arithmetic helper

Run with Python 3.10 or newer; standard library only, no network or writes:

```bash
python3 -B scripts/calculate_capacity.py < assets/example-capacity.json
python3 -B scripts/calculate_capacity.py --help
python3 -B scripts/test_capacity.py
```

These are internal helper commands, not additional public skill flags.
The example is synthetic arithmetic, not a recommended Nebius configuration.
Use a private temporary model for an assessment; put the relevant equations,
inputs and results in report calculation notes, not raw cloud data.

## Input contract

JSON object with exactly `schema_version: 1`, `pools`, `components`, `scenarios`.
Every field described below is required. IDs are unique within each list,
lowercase hyphenated names up to 64 characters. Unknown fields, duplicate keys,
negative/boolean/fractional/nonfinite numbers and omitted resource dimensions
are invalid. Input is capped at 2 MiB, arrays at 1000 entries, integers at 10^15.

All resource vectors have exactly these normalized integer fields:

| Field | Unit |
| --- | --- |
| `cpu_m` | Millicores; 1 vCPU/core = 1000 |
| `memory_mib` | MiB; 1 GiB = 1024 MiB |
| `ephemeral_mib` | MiB of node-local ephemeral storage |
| `gpus` | Whole devices of one compatible type within a pool |

Convert source quantities explicitly before input. Do not confuse GB with GiB;
round fractional MiB demand up and available capacity down. Unsupported sharing,
device types or placement constraints stay in the report, not hidden assumptions.

An **allocation** has `requests`, `limits`, `runtime`. Requests are a complete
vector; limits are a complete vector allowing `null` for unbounded CPU, memory
or ephemeral limits, never GPUs. The skill still recommends finite memory
limits; null supports auditing existing unbounded configurations. Runtime is
either a complete estimated simultaneous-demand vector or `null` (unassessed).
Finite limits must cover requests and the declared runtime estimate. GPU
requests and limits must match.

**Pools** have `id`, `allocatable` (per-node vector), `pod_slots`,
`overhead_pods` (per-node count), and `overhead` (per-node infrastructure Pod
allocation). Allocatable already excludes node OS/kubelet/eviction reservations.
Only Pods belong in overhead; never subtract the reservations twice. All usable
nodes in a pool are homogeneous; split incompatible shapes/types into pools.

**Components** have `id`, `pool` and `allocation` per effective Pod. Derive
init/sidecar stage maxima and configured Pod overhead first, using the target
Kubernetes version. This helper does not parse YAML or compute stage semantics.
For VM process groups, explicitly model their units and applicable slot ceiling
in the report; pool rows still use the same normalized arithmetic.

**Scenarios** have `id`, `nodes`, `replicas`, `budgets`:

- `nodes` maps **every** pool ID to `{base, surge, unavailable}` integer counts.
  Provisioned = base + surge; usable = provisioned − unavailable. Include
  draining/not-yet-ready nodes in unavailable. All provisioned nodes may still
  be billed and consume quota.
- `replicas` maps **every** component ID to its total concurrent Pod count,
  explicitly including rollout/terminating/job overlap. Use zero for inactive
  components. Nothing defaults from another scenario.
- `budgets` is a list (possibly empty) of `{id, unit, capacity, items}`.
  Each item is `{id, quantity, copies}`; demand = sum(quantity × copies).
  Units: `count`, `MiB`, `GiB`, `Mbps`, `IOPS`, `connections`.
  Capacity is a non-negative integer or null (unknown). A budget cannot mix
  units. Use separate live-data, backup, IP and traffic budgets as appropriate.
  This arithmetic does not infer retention, replication or effective quotas.

## Results and interpretation

Results contain per-scenario provisioned/usable nodes, request/limit/runtime
totals including infrastructure Pods, resource/Pod-slot margins, optional
budgets and issues. Spare percentage uses usable capacity as its denominator.
Zero capacity has no percentage. Unknown runtime or active unbounded limits
remain null, never zero. Inactive components do not make active totals unknown.

Each resource also exposes `known_subtotals` for requests, limits and runtime:
the sum of known terms with their scenario counts. Unknown terms are omitted
only from these partial sums, never replaced with zero in the complete totals.
Known subtotals that already exceed capacity produce violations even when the
complete total and margin remain null. A partial sum within capacity cannot
prove that the complete demand fits. Finite-limit excess remains a risk finding,
not a claim that the workload will consume its limit.

Exit 0 means `arithmetic_clear`; exit 1 means `violations_found`; exit 2 means
invalid input with a sanitized error. Limit excess is a risk finding rather
than proof of scheduling failure. Review all issue types in context.
`arithmetic_clear` is never "deployment feasible": placement and cloud
feasibility always remain `not_evaluated`, and null quantities stay unresolved.

This helper checks necessary aggregate and individual-Pod bounds, not bin
packing, taints, affinity, topology, PDBs, CSI semantics, provider compatibility,
quota inheritance, prices or performance. Do not treat an exit-0 result as
proof for those separate report checks.
