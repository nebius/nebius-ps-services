# Nebius assessment

## Read-only tenant boundary

Use the optional `nebius` skill and the argument arrays from
[input and scope](input-and-scope.md). A user-selected tenant automatically
triggers tenant-only SDK quota inspection; GPU designs also collect capacity
advice. Never enumerate projects, choose an arbitrary project, or inherit a
project from authentication. Keep the official Nebius Python SDK as the only
cloud interface for scripts; do not add a second auth/runtime implementation.

Honor explicit human/agent identity and profile selection. If selected access
is unavailable, report that limitation and finish the offline assessment.
Keep credentials in the existing protected store; never print, copy or replace
them. Preserve sanitized errors, scope, collection times and completeness.
Complete collection does not establish quota sufficiency or deployment readiness.

## Region coverage and catalog mapping

Omitted `--region` means `all`, even when the design imposes locality constraints.
Build coverage from current official documented regions plus region keys in
returned tenant quotas/advice. Show public-document coverage separately from
tenant-inspected coverage. Mark restricted-region eligibility unknown unless
verified by appropriate evidence. Missing rows or denied/incomplete inspection
never mean zero capacity, unsupported service or nonexistent region. Keep global
quota scope separate from regional keys; do not treat a global marker as a region.

For each region record service support, exact candidate platform/preset and its
CPU/RAM/GPU, storage/network constraints, source and timestamp. Use documented
quantities, not unit-looking preset names. Exact project platform eligibility
requires a later project-specific check and is outside this skill's public scope.
Do not turn published support or GPU advice into a project-verified catalog.

Map the same design demand into each region independently. Treat candidates as
alternative placements unless the design explicitly requires simultaneous
multi-region deployment. Show locality, service or fixed-platform conflicts as
excluded with reasons; never silently move workloads or redesign the stack.
A region that lacks a required component cannot pass because its CPUs fit.

Verify storage performance scaling, consistency, resizing, attachment and region
constraints. SFS-backed databases still need durability, locking/fencing and
independent data directories; shared storage does not establish HA. Separate
managed control-plane/service quantities from Compute workers and dependent
disk, filesystem, IP and networking demands.

## Demand and quota comparison

Key demand by tenant identity, exact provider quota name, region/scope and unit.
Aggregate shared consumers before comparison; never invent a RAM quota. Compare
known active tenant limits and measured usage, preserving unknown states and
unmapped resources. Missing tenant limits remain unresolved, not inherited from
an uninspected project. Label conclusions as tenant quota fit; project eligibility
and limits remain uninspected even when all tenant dimensions fit.

For a new deployment use the full incremental scenario envelope. For an existing
deployment, subtract reused resources only with explicit current evidence tying
them to this design and existing usage. Otherwise show the full-addition bound
and unresolved reuse; tenant aggregate usage is not attribution evidence. Do not
subtract future deletions before the resources can actually be released.

Show quota key/unit, limit, usage, headroom, incremental steady demand, scenario
maximum, multiplier, required free amount, deficit and timestamp. Apply global
quotas once across simultaneous consumers, not once per alternative region.

- **Sufficient:** all required tenant dimensions are known and fit; no claim
  about project eligibility or physical capacity.
- **Insufficient:** a confirmed tenant constraint has a deficit, even if other
  dimensions remain unresolved.
- **Unresolved:** applicable evidence is missing, stale, inaccessible or ambiguous.
- **Not inspected:** no live quota assessment was performed.

Keep known deficits visible alongside gaps. Use the offline quota planner for
`current usage + ceil(multiplier * incremental envelope)` with provider rounding.
Request submission is separately governed by [quota workflow](quota-workflow.md).

## GPU advice and physical capacity

The `nebius` owner's `scripts/inspect_capacity.py` uses the SDK's
`ResourceAdviceServiceClient` and `ListResourceAdviceRequest`. It collects all
pages with a region/fabric/platform/preset key and filters exact regions locally.
No region filter means all; do not pass literal `all` to an inspector. Responses
may omit resource metadata; any supplied parent must match the selected tenant.
An empty fabric is valid for shapes without a fabric-specific placement.

Retain each shape and allocation type (`on_demand`, `preemptible`, `reserved`)
with its data state, effective timestamp, availability level and VM count.
Fresh zero is measured zero; stale/unknown data or a missing timestamp produces
unknown counts. Collection can be complete while individual measurements remain
unresolved. Timestamped fresh advice remains a snapshot, not a creation guarantee.

Advice is already constrained by quota/reservation state. Do not subtract quota
usage again. Different presets or allocation types can describe shared physical
resources: never sum their available counts into a regional total. Compare exact
shapes separately; simultaneous mixed-shape feasibility remains unresolved
without independent joint-placement evidence. Reservation advice does not prove
that a future project can use a capacity block group.

Verified on 2026-10-26, Capacity Advisor covers GPU VMs and excludes CPU-only
VMs. Recheck official scope at assessment time. For CPU-only designs, skip this
collector and state physical capacity unverified. Other resource types without
an applicable authoritative capacity source also remain unverified. Never create
VMs, reservations, proof Pods or load tests to probe capacity.

## Recommendation and report

Group tenant totals and component mappings by region. Keep excluded regions
visible. Rank constraint-compatible candidates by complete scenario quota fit,
evidence coverage and verified cost; use latency only with evidence. Explain
unknowns rather than silently ranking them as zero or sufficient. Report no
verified fit when appropriate. Candidate totals and prices are alternatives,
not a combined bill. Give exact shortfalls and later project/provisioning checks.

## Official sources

Use the `nebiusDocs` MCP search and retrieval tools first when available.
If unavailable or insufficient, use available Tavily Search/Extract against
official `docs.nebius.com` pages. If that route is unavailable or insufficient,
disclose the limitation and use built-in official-page search/retrieval.
Documentation lookup needs no tenant credentials; do not install a server or
change authentication for it. Preserve stricter host documentation-routing rules.

Recheck only the services used by the selected design. Cite exact pages and
retrieval dates in the project report, not search result pages.

- [Regions and service availability](https://docs.nebius.com/overview/regions)
- [Platforms and presets](https://docs.nebius.com/compute/virtual-machines/types)
- [Project platform discovery](https://docs.nebius.com/compute/virtual-machines/list-platforms)
- [Capacity advisor](https://docs.nebius.com/compute/virtual-machines/capacity-advisor)
- [Quotas](https://docs.nebius.com/overview/quotas)
- [Compute quotas](https://docs.nebius.com/compute/resources/quotas-limits)
- [MK8s quotas](https://docs.nebius.com/kubernetes/resources/quotas-limits)
- [Storage types](https://docs.nebius.com/compute/storage/types)
- [Filesystem CSI](https://docs.nebius.com/kubernetes/storage/filesystem-over-csi)
- [Network requirements](https://docs.nebius.com/kubernetes/networking/requirements)
- [Compute pricing](https://docs.nebius.com/compute/resources/pricing)
- [MK8s pricing](https://docs.nebius.com/kubernetes/resources/pricing)
