---
name: sdlc-start
description: "Use only as part of the Agentic SDLC workflow; explicitly start from a direct objective or current plan, continue the same session's bound run, or explicitly continue a project across sessions. Own phase routing, checkpoints, accepted steering and completion."
disable-model-invocation: true
---

# Start SDLC

## Help

For `$sdlc-start --help` or `$sdlc-start -h` (including native Claude forms), return concise help and stop before
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

## Agent Compatibility

Before workflow reads, load `../global-context-management/references/agent-hosts.md`
for the selected agent home, native identity and required runtime setup.

Use `$sdlc-start` in Codex, `/sdlc-start` in Claude Code, or
`/skills:sdlc-start` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Coordinate one accepted objective through requirements, context, design, locked
plans, implementation, validation, delivery and optional authorized publication.
`global-context-management` owns context and continuity. `maintain-project-specs`
owns canonical interpretation and publication. This coordinator owns accepted
objectives, phase routing, execution and evidence.

`maintain-project-specs` is the sole semantic, schema, paired-publication, and
validation owner. Requirements and design skills write only as its Agentic SDLC
adapters; workers report specification gaps to the coordinator.

## Public Interface

```text
$sdlc-start
$sdlc-start <objective>
$sdlc-start continue [project-folder]
$sdlc-start -h
$sdlc-start --help
```

Bare invocation consumes an unambiguous current plan or the same native session's
validated active binding. A new session must use `continue`; it never inherits a
sole active run automatically. Select ambiguity by readable project and objective
descriptions. `continue` defaults to the current project folder. There are no
workspace, prompt-file, queue-management or compatibility commands.

## When To Use

- An explicit user invocation accepts an objective or current plan.
- The bound coordinator resumes a checkpoint or receives material steering.
- An explicit cross-session continuation selects an existing project run.

## When Not To Use

- Independent implementation belongs to `implement`; do not nest coordinators.
- Workers must follow their assignment and report specification gaps.
- Ordinary project-spec maintenance does not require an SDLC run.

## Inputs

Normalized accepted outcome, scope, constraints, acceptance criteria,
clarifications and provenance; canonical specs; effective instructions; validated
session binding; phase checkpoints; locked plans and immutable execution bases.
Do not archive raw conversation turns or capture prompts globally.

## Required Reads

- `../worktree/references/workflow-contract.md` for objective, binding, basis,
  scheduler and reconciliation contracts.
- `references/objective-refinement.md` and `references/state-schema.md`.
- Applicable project instructions, canonical requirements/design and the current
  checkpoint. Requirements/design may be absent at first intake.
- Active repair-control records, evidence and execution registration before
  choosing a next phase. Use the registered integration checkout during prepared
  execution and the delivered checkout after promotion.

## Writes

Private accepted objective revisions and bindings through Worktree-owned helpers;
phase state, feature queue within this objective, checkpoints, fingerprints and
bounded evidence. Canonical specs publish only through maintain-project-specs at
the registered checkout and expected revision. Workers never publish them.

## Process

1. Resolve the exact project and applicable instructions. Require existing named,
   committed Git history. Before canonical authoring, the shared intake helper verifies SDLC remote-default prerequisites and selects its feature branch. Preserve ordinary
   managed outer-worktree ownership; greenfield bootstrapping is outside this skill.
2. Normalize direct intent in a private candidate. Call
   `../worktree/scripts/workflow_intake.py --owner agentic-sdlc accept` with the
   project and candidate. Bare bound continuation uses `observe`; explicit
   cross-session continuation uses `continue`. These are private helper actions.
   Observation restores an existing binding after compaction and starts no work.
3. Accept material steering with `accept --revise`, which fences dispatch and
   promotion before recording the adjacent objective revision. Stop affected
   native writers, record termination, reconcile them without admitting stale
   results, retire affected waves, classify spec impact and replan. Preserve
   earlier evidence and commit history. Lift the fence only through
   `resume-replanned`. Cancellation uses `fence --reason cancelled`, writer
   reconciliation, then `cancel-complete`; never roll back delivered commits.
4. Apply objective refinement. Preserve stable clarification IDs; block design
   and planning only on material unresolved questions. Use the requirements and
   design adapters to publish applicable canonical changes. Tasks with no spec
   impact do not invent documents. Optional live environments are useful but
   absence alone does not block requirements.
5. After requirements publication, use private `refinement-ready`. After design
   and before planning, use private `settle-objective` with a complete owner
   impact claim. Requirement/design mappings, execution-only changes and justified
   no-effect outcomes remain explicit. The claim grants no execution authority.
6. Resume phase routing below using the exact bound run. A feature queue is only
   a dependency/prioritization queue inside the accepted objective; unrelated
   requests are never appended to it.

- Resume from `checkpoints/latest.json` first. Do not rely on conversation
  memory for current feature, phase, blocker, retry counts, or next skill.
- Reconcile `current-state.json`, the latest checkpoint, feature queue,
  fingerprints, and evidence. If they disagree, prefer the newest complete
  checkpoint that matches existing evidence, then repair `current-state.json`.
- Initialize local run state only when no active run or checkpoint exists.
- If requirements are missing, route to `sdlc-create-requirements`.
- If design is missing or stale and required context is missing, route only to
  `sdlc-gather-context`.
- If design is missing or stale and required context is present, route only to
  `sdlc-create-design`.
- Before design exits and before planning, require
  `scripts/validate_project_specs.py` to validate the exact canonical v2 pair.
  Structural or traceability defects route to the owning requirements/design
  adapter before spec-dependent planning. The resulting receipt describes
  project truth but never authorizes auto-steering, execution, cleanup,
  finalization, or Stop.
- After design is ready, require the design adapter's initial canonical-pair
  admission when files are new: only the selected pair may be dirty, guarded
  repo-root staging makes them tracked, and HEAD remains unchanged. Do not
  advance untracked specs or move the preparation phase's commit earlier.
- After design is ready and before planning, write the complete private impact claim
  and invoke private `workflow_intake.py --owner agentic-sdlc settle-objective`. Require the shared
  owner to bind the latest accepted identity and every extracted statement
  occurrence to the exact current canonical specs and publish the immutable impact receipt.
  Publication is per-run locked, compare-checks the ledger, and preserves then
  skips conflicting crash orphans. An existing execution without a basis receipt
  must use a distinct safe replan for material impact; never relabel unchanged
  plan bytes as current evidence. Requirements readiness alone never unlocks
  planning, steering resolution, execution, integration, or promotion.
- Read every already-effective `AGENTS.md` in the selected instruction chain.
  Do not create, edit, retire, or reload project instructions automatically.
  A user-invoked project-instruction workflow remains separately responsible
  for its own mutation safety and cannot become an SDLC prerequisite.
- Build or refresh the feature queue from `docs/design.md`.
- Check `STEERING.md` and `steering/auto-steering.json` before every
  iteration.
- If there are new or unresolved steering entries, stale steering fingerprints,
  conflicting reminders, or changed requirements, design, context, plan, or
  evidence reminders, route to `sdlc-auto-steering` before selecting the next
  implementation phase.
- Honor `sdlc-auto-steering` dispositions: route `requirements-change` to
  `sdlc-create-requirements`, `design-change` to `sdlc-create-design`,
  `docs-update` to `sdlc-update-documents`, and `needs-human` to a blocked
  human-input state. A `project-agent-instructions-change` disposition is
  advisory unless the current user explicitly requested the separate mutation
  workflow. Only in that explicit case route to `project-agent-instructions`;
  otherwise record the recommendation and continue under already-effective
  instructions.
- Select the highest-priority incomplete feature whose dependencies are complete.
- Keep exactly one feature execution active. After `plan_locked`, route to
  `sdlc-prepare-execution`; after `execution_prepared`, route to `sdlc-tdd` in
  the recorded integration cwd. From TDD through alignment, require phases to
  use that integration checkout and bind evidence to its recorded HEAD.
- During implementation, route repeatedly to `sdlc-implement-plan` until every
  dependency wave is integrated, combined evidence passes, and worker cleanup
  completes. Task agents may run concurrently only inside the current wave.
  Their assignments inherit the accepted root-intent digest and exact spec
  receipt. Workers do not reclassify intent or edit specs; typed `spec_gaps`
  return to the root coordinator for adapter reconciliation and replanning.
- On every gate failure, require an immutable normalized failure event and
  route to `sdlc-classify-failure`. Preserve the failed criterion, exact
  integration commit, environment identity, reproduction oracle, evidence
  digests, and requirements/design/plan fingerprints.
- Let proven mechanical causes bypass diagnosis. Route an ambiguous,
  persistent, intermittent, or cross-boundary failure to `troubleshoot`
  exactly as a conditional diagnostic branch. Persist its diagnosis pointer
  and return every diagnosis to `sdlc-classify-failure`; troubleshooting never
  chooses or executes the repair phase directly.
- Treat `repair-control-v1` as authoritative for blocker identity, route
  history, invalidations, active time, and repair dispatch budgets. Freeze new
  worker dispatch while classifying, diagnosing, or replanning. Stop on missing
  evidence, unresolved competing hypotheses, policy/human decisions, semantic
  routing cycles, or budget exhaustion.
- A successful repair with invalidations enters the repair-control
  revalidation cursor. Route only to its exact next gate, bind each completion
  to digest-verified evidence and current fingerprints, use the live
  integration HEAD before commit, and bind final commit evidence to completed
  promotion plus a clean promoted checkout on the recorded base branch after
  verified integration cleanup. Do not permit UAT, PR, or publication until
  the cursor is complete.
- For a proven implementation defect, use the active task's existing attempt
  contract only while that task is active. After waves complete, require an
  immutable corrective plan vN+1 and private `replan-future`, preserving full
  completed task definitions/digests and resource-owning waves while appending
  correction tasks and waves.
- Never reopen sealed, promoted, or completed execution. A post-promotion
  failure begins a corrective run from the exact promoted commit.
- Admit failure-driven redesign only after the classifier validates positive
  system-contract proof and the required approval mode. Missing or
  inconclusive implementation evidence is not a design defect.
- Route to `sdlc-commit` only after downstream evidence passes. That phase owns
  final integration sealing, exact fast-forward promotion to the unchanged
  project branch, and integration-resource cleanup. UAT and PR phases use the
  promoted project checkout.
- Set one `next_recommended_skill` based on the current phase.
- After evaluation passes, route to `sdlc-update-documents` when feature-facing
  docs, changelog, examples, or documentation steering need updates, then use
  the general `align` quality gate before commit readiness.
- After UAT, post-promotion documentation assessment is read-only. Route to
  `sdlc-update-documents` only for assessment and no-op evidence. Required edits
  return `DOCUMENTATION_DRIFT` through `sdlc-classify-failure` and start a
  corrective run from the exact promoted commit. Preserve completed execution;
  require fresh documentation, alignment, promotion and UAT before handoff.
- In a managed outer worktree, invoke the private `release-outer-lease`
  transition only after final alignment, UAT, and documentation evidence are
  recorded, the exact promoted child HEAD is clean, and all Agentic SDLC
  integration and worker resources are absent. Record phase
  `outer-integration-pending`, return the recorded primary path/source branch
  plus the exact `$worktree integrate <generated-name>` command, and stop. Do
  not invoke or auto-continue the explicit-only `worktree` skill; wait for a
  fresh user invocation from that primary checkout. After independently
  observing local integration, invoke private
  `complete-outer-integration` to bind the source merge proof; never push the
  child or open a PR from it.
- In an unmanaged final source checkout, treat the shared PR skills as
  restricted SDLC modes: `create-pr` may publish only the clean exact promoted
  SHA, and `review-pr`
  may record findings/readiness without changing the PR head. Any required
  branch change returns through `sdlc-classify-failure` and this coordinator.
  `sdlc-merge-pr` may merge only after an explicit user request and only while
  the remote PR head still equals that promoted and reviewed SHA.
- Before returning a next skill, write a checkpoint containing current feature,
  phase, status, blocker, retry counts, last successful phase, fingerprints,
  evidence pointers, repair event/diagnosis/control pointers, and next
  recommended skill.
- Update `checkpoints/latest.json`, then `current-state.json`, after the
  checkpoint is written.
- Write a history entry only when state changes. A repeated resume with no
  state change returns the existing checkpoint and does not duplicate history.
- Stop when complete, blocked, retry budget is exceeded, or human input is
  required. Once every accepted feature is delivered and final phase evidence is
  complete, call private `workflow_intake.py --owner agentic-sdlc complete`.
  Do not advance any unrelated-objective queue.

## Idempotency

Identical normalized intent reuses its revision and bound run. Observation is
read-only. Resume reconciles exact checkpoint and resource identities before
advancing. Completed work remains historical; a distinct objective starts a fresh
run. Old state fails `WORKFLOW_UPGRADE_REQUIRED` and remains untouched.

## Failure Handling

Reject stale bases at dispatch, admission, integration and promotion. Unknown
ownership, changed source HEAD, dirty promotion source or unverifiable worker
termination retains the result and reports the blocker. Native host capacity
that cannot be established falls back to one isolated worker. Do not turn
fixture intervention or missing runtime evidence into a pass.

## Must Not

Do not capture prompts, infer ownership from a sole run, retain command aliases,
convert old state, overwrite unrelated dirt, independently publish specs from
workers, nest coordinators, bypass repair budgets, or authorize remote actions
from local completion. Git worktree locks do not establish writer quiescence.

## Completion Criteria

Every accepted feature has current basis-bound evidence, combined validation,
alignment, promotion and cleanup. SDLC retains TDD, unit/UI tests, evaluation,
UAT, documentation and exact-commit publication responsibilities. After promotion,
documentation assessment is read-only; drift starts a corrective run.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Report readable objective and project, current feature/phase, immutable basis,
actual observed concurrency, evidence and blockers, delivered checkout/commit,
retained recovery resources and the next recommended skill. Separate deterministic,
installed-hook, native-agent and live-application evidence.

## References

- `references/state-schema.md`
- `references/objective-refinement.md`
- `../worktree/references/workflow-contract.md`
