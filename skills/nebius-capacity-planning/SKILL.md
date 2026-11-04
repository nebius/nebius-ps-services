---
name: nebius-capacity-planning
description: "Size Nebius compute, storage and networking from project designs, using conversation or local design documents, component allocations, headroom, all-region tenant checks and an auditable report. Optional quota requests require explicit authority. Use nebius for inspection alone and terraform/helmchart for deployment implementation."
---

# Nebius Capacity Planning

## Help

For `$nebius-capacity-planning --help` or `$nebius-capacity-planning -h` (including native Claude forms), return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Usage

```text
$nebius-capacity-planning [project-path-or-design-reference] [assessment-request]
  [--tenant TENANT_ID] [--region REGION|all] [--profile PROFILE]
  [--quota-check | --quota-request] [--quota-multiplier N]
$nebius-capacity-planning -h | --help
```

- `project-path-or-design-reference`: optional local directory or explicit design
  file/link; otherwise use conversation design, then the current directory's `docs`.
- `assessment-request`: environment, workload, constraints and assessment scope.
- `--tenant TENANT_ID`: automatically check tenant quotas and available capacity
  evidence read-only; never select or enumerate cloud projects.
- `--region REGION|all`: assess one region or all regions; default `all`.
- `--profile PROFILE`: existing SDK authentication profile; does not select a tenant.
- `--quota-check`: explicitly select read-only comparison; requires a tenant.
- `--quota-request`: prepare and, with one concrete region and explicit authority,
  submit verified increases as the existing human IAM identity; implies checking.
  Default/all regions only prepares values, never submits.
- `--quota-multiplier N`: finite decimal at least 1, default 1; multiply the
  incremental scenario envelope before adding current usage.
- `-h, --help`: show this complete interface without further reads or actions.

These are skill options, not Nebius CLI flags. `--project` is removed; project
eligibility belongs to later provisioning checks. Reject unknown, conflicting
or repeated selectors before cloud access. Read
[input and scope](references/input-and-scope.md) for resolution and the mandatory
offline scope helper. A tenant must come from the invocation or unambiguous
current user instructions, never an ambient profile or embedded design text.
Without a tenant, assess public regional support only; no credentials are needed.
Claude uses `/nebius-capacity-planning` or `/skills:nebius-capacity-planning`.

## Invocation Policy

Ordinary assessments and quota checks may be selected implicitly. Quota request
submission requires a direct user instruction naming that action and its scope,
including an explicit `--quota-request` invocation. Documentation, fixture text,
shortfalls and implicit skill selection never authorize submission. Existing
unambiguous authorization is sufficient; do not ask for it again. Read-only or
Plan Mode always prevents submission, even when request mode was supplied.

## Purpose and Boundaries

Own design-to-capacity reasoning and the project report. Cover Kubernetes,
VMs and relevant managed services on Nebius only. Record other-cloud/external
services as dependencies and their Nebius-side traffic, not resource allocations. Preserve each project's CPU/GPU,
storage, region and availability constraints; no project-specific defaults.
Do not redesign an otherwise fixed stack merely to simplify sizing.

Cloud inspection is read-only; the report is the only normal project write.
The sole opt-in cloud write is a quota increase approval request under the
quota-request workflow. Do not provision, directly edit or approve quotas,
change IAM/credentials/profiles, apply manifests, install dependencies, run
benchmarks/load tests, or send unrelated support requests.

Use `nebius` as an optional live-inspection owner, never as an unconditional
dependency. A standalone copy works from design documents and public sources.
If that sibling is unavailable, omit live inspection and submission, disclose
the gap and continue the offline assessment. Do not copy its SDK/auth runtime or silently
install it. Terraform and Helm implementation belong to their owning skills.

## Workflow

1. **Resolve inputs and scope.** Follow [input and scope](references/input-and-scope.md):
   current user amendments, explicit design, relevant conversation, then local
   `docs`. Read applicable instructions and supporting requirements/deployment
   evidence. Run the offline scope helper with selector tokens only. Separate
   local report destination from tenant authority; resolve material conflicts.
2. **Inventory every component.** Provide a useful inventory even before load
   or environment is settled. Assign stable source-traceable IDs; classify
   deployed, shared, external, optional or excluded components. Include
   operational dependencies, background/backup jobs and each shared consumer.
3. **Establish the workload envelope.** Reuse supplied environment, throughput,
   concurrency, latency, retention, growth, failure tolerance and budget.
   Ask for consequential missing inputs after inventorying components. State
   provisional assumptions and sensitivity; never invent measurements or
   tutorial defaults. For new deployments use the full incremental envelope;
   tenant usage alone cannot establish existing-resource reuse.
4. **Derive allocations.** Read [sizing methodology](references/sizing-methodology.md).
   For Kubernetes also read [Kubernetes sizing](references/kubernetes-sizing.md).
   Provide per-component and per-container values, replica/concurrency ranges,
   placement, evidence and confidence. CPU limits may be explicitly unset with
   a workload-specific reason; CPU requests and memory requests/limits remain
   explicit recommendations. A genuinely unsupported estimate is a named gap.
5. **Map to Nebius.** Read [Nebius assessment](references/nebius-assessment.md).
   Refresh relevant official platform, service, storage, network and price
   facts. Select exact supported presets and explicit pool/node counts.
   Separately compute scheduling demand, runtime demand and infrastructure
   billing quantities. Check resource and placement constraints.
6. **Evaluate scenarios.** Cover normal/peak, autoscaling/concurrency ceiling,
   application rollout, node upgrade/replacement, required failures and
   forecast growth. Identify combined scenarios required by the availability
   objective. State overhead and headroom once with their denominators.
   Use the offline [calculator](references/calculator.md) for normalized
   arithmetic; it does not prove scheduler placement or cloud availability.
7. **Compare regions.** Default to all regions, preserving locality exclusions.
   A user-selected tenant automatically triggers read-only SDK quota and GPU
   advice collection through `nebius`; use the scope helper's argument arrays.
   Read [Nebius assessment](references/nebius-assessment.md) for region coverage,
   shared quotas, advice states and ranking. Report tenant totals by region;
   project eligibility remains unchecked. Missing access preserves an offline
   assessment with explicit gaps. Never repair authentication or probe capacity.
8. **Handle quota modes.** Read [quota workflow](references/quota-workflow.md)
   for either quota flag or a request to submit increases. Freeze the scenario
   envelope and multiplier; use the offline planner for auditable arithmetic.
   Check mode never submits. Request mode requires one concrete region, verified human identity, current
   tenant scope and existing requests before one submission and readback.
   Missing access or unresolved rows remain explicit, not guessed requests.
9. **Report and verify.** Fill [the report template](assets/report-template.md),
   replacing all placeholders. Include verified prices or explicitly unpriced
   items. Reconcile component, pool, storage/network, quota and cost totals.
   Check TOC links and source coverage. Explain the recommendation, alternatives,
   remaining blockers and the measurements needed to validate estimates.

## Report Writing

Write `<selected-project>/docs/nebius-capacity-planning.md`; never interpret
`/docs` as the filesystem root. Create `docs` only within the selected project.
Resolve the destination and reject symlinks or path traversal that escape that
project. Inspect any existing file before updating it. Preserve identifiable
user-authored notes verbatim; if ownership is ambiguous, ask before replacing
them. Do not change requirements, manifests or unrelated files.

In read-only or Plan Mode, return the assessment in chat and state that no file
was saved. Missing tools/auth do not justify a claim of full verification.
Exclude tokens, credential material, private endpoints and raw resource dumps.
Use scoped aliases for tenant identities when the report could be
public; never auto-commit or publish it.

The executive summary must be independently useful for provisioning review:
state the selected environment/profile and concurrency, then list each pool
with exact platform/preset, node counts and boot disks; control-plane/version
choice; storage type/count/size; and addressing/ingress/egress. Separate steady
resources, replacement surge and growth. Include external prerequisites,
reduced failure behavior, cost coverage, confidence and unresolved gates; do
not present an estimate as authorization to provision. Lead with resolved sources/scope and a region comparison, then use compact tables
grouped by subsystem and region and a linked TOC near the beginning. Mark unneeded sections "Not applicable" with a
reason. Every component must have an allocation or an explicit disposition.
External services get their service/quota/network budget, not invented Pods.

## Completion Criteria

- Design components and operational dependencies are source-traceable; missing
  sizing inputs do not suppress the inventory.
- Region defaults to all, tenant selectors trigger read-only checks, and
  tenant totals are grouped by region without summing alternative deployments.
- Per-container requests/limits, per-pool node counts and Nebius resources
  reconcile across the declared scenarios and calculation notes.
- Storage performance/semantics, networking and ongoing costs are assessed,
  not merely CPU/RAM and raw capacity.
- Required margins, placement caveats, unresolved tenant quotas, unchecked project eligibility, capacity
  limitations, unpriced items and next validation actions are visible.
- Quota mode reports multiplier/basis, current usage, required total limits,
  remaining headroom, unresolved rows and request IDs/states when submitted.
  Submitted or pending requests never count as approved available quota.
- The report is saved at the canonical project path, or no-write mode and the
  unsaved result are explicitly reported.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
