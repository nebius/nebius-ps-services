---
name: sdlc-create-plan
description: "Use only as part of the Agentic SDLC workflow; use when one ready feature needs a locked dependency-safe plan, or a classified post-evaluation repair needs immutable corrective plan vN+1 with preserved completed task definitions and appended corrective waves."
---

# Create Plan

## Purpose

Create a local, locked execution plan for exactly one feature.
Preserve vertical end-to-end feature slices while expressing implementation as
a dependency-safe task graph.

## When To Use

- A ready `FEAT-*` needs an execution plan.
- Design or context changed and the old plan must be superseded.
- The SDLC loop reaches the planning phase for the current feature.
- A proven post-evaluation implementation defect occurs after execution waves
  completed and requires corrective plan vN+1.

## When Not To Use

- Do not use for requirements or design authoring.
- Do not use to write tests or code.
- Do not use to create committed docs.

## Inputs

- One `FEAT-*`.
- Corresponding `REQ-*` blocks.
- Context pack.
- Current repo and feature state.
- For correction: `diagnosis-v1`, original regression oracle, failed criterion,
  current execution lifecycle, and completed task manifest.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- `context/FEAT-*.context.md`.
- Existing source, tests, and local plans for the feature.
- Active repair-control, diagnosis, execution coordinator, completed task
  records, and result digests when creating a correction.

## Writes

- `plans/FEAT-*.plan.vN.md`.
- `plans/FEAT-*.plan.vN.lock`.
- Plan fingerprint and state transition to `plan_locked`.
- `corrective-plan-validation-v1` from
  `scripts/corrective_plan.py` for a corrective version.

## Process

- Use `assets/templates/feature-plan.md.template` for plan shape.
- Confirm feature status is ready and dependencies are complete or explicitly allowed.
- Identify files to inspect, create, modify, and avoid.
- For serial multi-layer application features, plan one end-to-end slice around
  the feature's behavior and layer map. Group test-first, implementation,
  validation, and evaluation steps by behavior across layers rather than by
  isolated layers. Use foundation-only steps only when they are true blockers.
- Define test-first, implementation, validation, evaluation, rollback, and stop-condition steps.
- Replace flat implementation steps with stable `TASK-*` records. Each task
  must name requirements, dependencies, exact/prefix write claims, conflict
  domains, focused validation, done criteria, and rollback/stop conditions.
- Serialize tasks that share files, path prefixes, APIs, schemas, migrations,
  dependency manifests, shared abstractions, infrastructure identities,
  exclusive test resources, or external mutations. Mark uncertain ownership
  `unknown`; never infer parallel safety from missing data.
- Record planned dependency waves for human review. Treat them as informative:
  `sdlc-prepare-execution` recomputes and verifies the graph before mutation.
- Lock the plan.
- For a post-evaluation correction, create only adjacent immutable plan vN+1.
  Set `Plan kind: corrective`; bind the exact superseded plan, diagnosis,
  original regression oracle, and completed task manifest digest.
- Preserve every existing task definition byte-for-byte and every completed
  task digest exactly. Append contiguous corrective `TASK-*` IDs and dependency
  waves; each corrective task must reference the diagnosis and regression
  oracle. Never reinterpret completed work under a new plan digest.
- Run `scripts/corrective_plan.py` before routing to execution. If append-only
  correction cannot preserve history safely, stop for human direction.

## Idempotency

- If design and context fingerprints are unchanged, reuse the existing locked plan.
- If design or context changed, create a new plan version.
- Never modify an existing locked plan.
- If execution already exists, mark the old coordinator `REPLAN_REQUIRED` and
  create a new locked plan version. Preserve started assignments, results,
  commits, and worktrees until the coordinator can prove a safe reconciliation;
  never reset or silently reuse them against a new plan digest.
- Freeze dispatch while corrective replanning. Resource-owning waves remain
  immutable; only resource-free planned future waves may be replaced.

## Failure Handling

- If design is not implementable, route to `sdlc-create-design`.
- If context is missing, route to `sdlc-gather-context`.
- If project tooling is unknown, route to a relevant project skill.
- If completed definitions, digests, or plan lineage cannot be preserved, stop
  with `HUMAN_INPUT_REQUIRED`; do not synthesize a compatibility plan.

## Must Not

- Commit the plan.
- Edit locked plans.
- Implement code or write tests.
- Expand feature scope.
- Reopen sealed, promoted, or completed execution.

## Completion Criteria

- Local plan exists and is locked.
- Plan contains test, implementation, validation, and evaluation steps.
- Every implementation unit has a stable, complete `TASK-*` assignment record.
- Dependencies are acyclic and parallel candidates have disjoint write claims
  and conflict domains.
- Plan identifies the end-to-end slice or records why no vertical slice applies.
- Plan references exact feature and requirement IDs.
- A corrective plan passes the append-only validator and names the original
  evaluation oracle.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only `sdlc-create-design`
  writes `docs/design.md`. Other skills route spec changes to those owners.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different SDLC shape.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Use `assets/templates/feature-plan.md.template` when creating the corresponding artifact.
- Use `scripts/corrective_plan.py` before locking or dispatching a corrective
  plan version.
