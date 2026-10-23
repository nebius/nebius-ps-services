# Nebius assessment

## Read-only discovery boundary

Use the existing `nebius` skill when available for tenant/project selection,
bounded API/CLI reads and sanitized inspection envelopes. Read its selection,
authentication and quota guidance before querying. A tenant-only request
permits scoped project discovery, not choosing an arbitrary project for
project-specific sufficiency. Ask which project or label the result tenant-only.

Honor explicit user identity/profile selection. An ambient agent token is not
an acceptable fallback when the user selected human authentication. Keep
credentials in the existing protected store/runtime; do not print tokens, pass
them on command lines, copy them into fixtures or replace profiles. If selected
authentication is unavailable, report that limitation and finish offline work.

The Nebius inspection envelope uses `scope`, `data`, `complete`, `errors`.
Collection completeness is not sizing sufficiency. Preserve sanitized errors
and partial results without raw resource dumps. The current compute inventory
helper describes inventory metadata; it is not a sizing/preset catalog.
Resolve platform/preset specifications using the documented platform API
through that owner, verifying the installed CLI/SDK surface before use.

## Catalog and compatibility

For every candidate record region, project scope when verified, service,
platform, preset, CPU/RAM/GPU, relevant network/storage constraints and source
timestamp. Public docs establish documented support; project platform listing
establishes project availability; neither proves spare physical capacity.
Use actual returned quantities: unit-looking preset labels do not substitute
for the documented/API memory units.

Verify storage types, performance scaling, access/consistency, resizing,
attachment limits and region/project boundaries. For SFS-backed databases,
explicitly assess durability, locking/fencing and independent data directories;
shared storage does not make a database HA. Record qualification gaps.

For managed Kubernetes distinguish managed cluster/control-plane charges and
quotas from Compute worker quantities. Include cluster/node-group limits and
dependent Compute, filesystem, disk, IP and networking quotas.

## Demand and quota comparison

Build a demand map keyed by the provider's quota identifier, scope, region and
unit. Aggregate all consumers of a shared dimension before comparing. Do not
invent a RAM quota merely because RAM is a sizing dimension.

Use the current `nebius` owner's effective quota semantics. Applicable measured
tenant and project headroom constrain the same allocation. Missing limits may
inherit only when verified semantics establish that; unknown state/usage,
denied scope or unmapped resources are unresolved, never implicitly unlimited.

For an existing deployment, distinguish reused resources, net additions and
temporary overlap. Do not compare the full target fleet with remaining quota
if that fleet already includes charged existing nodes. Conversely, do not
subtract resources slated for deletion before they can actually be released.

Show quota ID/unit, scope/region, limit, current usage, effective headroom,
incremental steady demand, scenario maximum, deficit, status and timestamp:

- **Sufficient:** all relevant applicable constraints are known and fit.
- **Insufficient:** at least one confirmed constraint has a deficit.
- **Unresolved:** missing, stale, incomplete or ambiguous applicable evidence.
- **Not inspected:** no live quota assessment was performed.

Keep known deficits visible even when other dimensions are unresolved. Suggest
the needed target quota (`current usage + maximum incremental demand`, plus
any separately justified future allowance) without submitting requests.

## Region ranking and physical capacity

First reject candidates violating required locality, latency, service,
preset, storage or fabric constraints. Then compare quota fit for the complete
required scenario, evidence confidence, spare bottleneck capacity and cost.
Do not rank regions by free CPUs alone or turn unknowns into zero/unlimited.
Separate tenant-level candidates from project-verified candidates.

As documented on 2026-10-20, the capacity advisor covers GPU VMs and excludes
CPU-only VMs. Its timestamped GPU advice is not a provisioning guarantee.
Recheck this scope at assessment time. If no suitable API establishes CPU
capacity, state "physical capacity unverified" and recommend provider
confirmation; never create test VMs or reservations to probe it.

Present one justified recommendation and meaningful alternatives with exact
quota increases, tradeoffs and unresolved gates. Reserved/spot capacity and
commitments are recommendations only, with interruption and cost implications.

## Official sources

Use the `nebiusDocs` MCP search and retrieval tools first when available.
If unavailable or insufficient, consult official `docs.nebius.com` pages
directly and disclose the fallback. Documentation lookup needs no tenant
credentials; do not install a server or change authentication for it.

Recheck only the services used by the selected design. Cite exact pages and
retrieval dates in the project report, not search result pages.

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
