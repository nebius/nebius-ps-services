---
name: sdlc-start
description: "Use only as part of the Agentic SDLC workflow; use when the user explicitly invokes `workspace init` or `run` with a managed prompt path or unique filename to initialize, start, resume, continue, or steer a prompt-bound Agentic SDLC run. This is the main SDLC coordinator and state-machine skill."
---

# Start SDLC

## Purpose

Coordinate the SDLC loop by reading specs, checkpoints, steering input, and
local state, selecting the next feature, refreshing auto-steering when needed,
and choosing exactly one next skill.

## Public Interface

Expose exactly these two actions:

```text
$sdlc-start workspace init [project-folder]
$sdlc-start run <prompt-path-or-unique-filename>
```

- `workspace init` defaults to the exact current folder and may run before Git
  is initialized.
- `run` accepts one managed absolute prompt path or unique managed filename.
- Steering means editing the same prompt and repeating `run`.
- Do not expose bare resume, prompt IDs, run IDs, phase actions, aliases, or a
  separate steering command.

## When To Use

- The user explicitly invokes one of the two public actions.
- A Stop-hook continuation prompt repeats `run` with the bound prompt filename.
- An active run needs the next phase selected after a skill completes.

## When Not To Use

- Do not use to implement code directly.
- Do not use to free-edit requirements or design.
- Do not use to commit, push, create PRs, review PRs, or merge directly.

## Inputs

- `docs/requirements.md`.
- `docs/design.md` when present.
- The selected project root's active `AGENTS.md`, `AGENTS.override.md`, or
  configured fallback instruction file when present.
- Existing local run state when present.
- The bound managed prompt and its latest immutable accepted revision.
- Active `STEERING.md` and `steering/auto-steering.json` when present.
- Optional user-provided live experiment environment information for
  requirements capture.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- `references/prompt-workspace.md` before prompt initialization, intake,
  binding, steering, or legacy-run decisions.
- `~/.codex/sdlc-runs/<project-id>/active-run.json` when present.
- Active run state under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- `current-state.json`, `feature-queue.json`, `fingerprints.json`,
  `checkpoints/latest.json`, and the latest checkpoint file.
- `STEERING.md`.
- `steering/auto-steering.json` when present.
- The latest private `project-agent-instructions` manifest, decision, and state
  when present.
- Latest feature evidence and failure logs.
- The active `failure-event-v1`, optional `diagnosis-v1`,
  `repair-control-v1`, append-only repair journal, and current repair pointer.
- Active feature execution coordinator and registered worktree state when
  preparation has started.
- `references/state-schema.md` for state fields and transitions.
- `assets/hooks/README.md` when maintaining optional SDLC PreToolUse or Stop
  hooks.

## Writes

- `active-run.json` only when starting or switching the active run.
- Project-level `workspace.json`, `activity.json`, managed prompts, and the
  editor workspace through the private prompt helper.
- Run-level `prompt.json` and immutable `inputs/rNNNN/prompt.md` snapshots
  through the private prompt helper.
- `run.json`, `current-state.json`, `feature-queue.json`, and
  `fingerprints.json`.
- `checkpoints/checkpoint-*.json` and `checkpoints/latest.json`.
- `history/iteration-*.md` for state transitions.
- Local blocker summaries.
- Repair pointers in current state and checkpoints. Structured repair records
  remain owned by the deterministic `sdlc-classify-failure` helper.
- Checkpoint pointers to execution state; Git resource mutation remains owned by
  `sdlc-prepare-execution`, `sdlc-implement-plan`, and `sdlc-commit`.
- Private project-instruction decision pointers and fingerprints returned by
  `project-agent-instructions`. That skill exclusively owns any generated
  project-root `AGENTS.md`.

## Process

- Read `references/state-schema.md` before initializing or repairing local state.
- For `workspace init`, invoke private `prompt_workspace.py init`; create or
  verify the exact project workspace, preserve all prompts and history, create
  one starter only when empty, optionally open the CODE/PROMPTS editor
  workspace with `Agentic SDLC: New Prompt` and `Agentic SDLC: Prompt History`
  tasks, report metadata-only history, and stop without starting a run. The
  private `new`, `list`, and `verify` actions are editor/state helpers, not
  additional public skill commands.
- For `run`, invoke private `prompt_workspace.py intake` before reading or
  changing phase state. Trust only its validated bound run, revision, digest,
  and snapshot; never use the editable prompt directly after intake.
- Route `new` into normal run initialization, `resume` through checkpoint
  reconciliation, `steering` only to `sdlc-auto-steering`, and `done` to
  `ALREADY_COMPLETE`. A different prompt cannot replace an unfinished run.
- When initializing or resuming, mirror the bound prompt filename, ID, latest
  revision, digest, and snapshot pointer into `run.json`; fail closed if that
  mirror disagrees with `prompt.json`.
- Accept an exact manual prompt rename only when the old source is absent, the
  prompt ID is unique, and bytes equal the bound revision. Repair the binding
  and run mirror before continuation. Reject rename-plus-edit, duplicate/stale
  copies, or digest disagreement; rename first, run once, then edit and rerun.
- Require `sdlc-auto-steering` to link the accepted prompt ID, revision,
  digest, and snapshot in its ledger, then call private `steering-resolve`
  after recording an `applied`, `blocked`, or `no_effect` disposition.
- At the beginning of a new run, or when `docs/requirements.md` is missing or
  lacks a Live Experiment Environment section, encourage the user to provide a
  non-production or disposable environment with safe connection steps, allowed
  actions, reset instructions, and evidence limits. Do not block the workflow
  only because the section is not provided.
- If the user provides live experiment environment details, route that update to
  `sdlc-create-requirements`; `sdlc-start` must not write `docs/requirements.md`
  directly or store raw environment details in local run state.
- Use the project ID returned by the validated workspace or intake result and
  read its `active-run.json` before choosing a run. Never recompute prompt
  workspace identity from repository metadata.
- Acquire or honor the active run lock. If the lock appears stale, report the
  lock owner and timestamp and recover only when the local evidence clearly
  shows no active writer.
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
- Once both requirements and design are current and validated, route to
  `project-agent-instructions` before auto-steering, feature planning, or
  execution. Pass the exact selected project root, `agentic-sdlc` ownership,
  and a caller-owned private state directory. Continue only after `created`,
  `refreshed`, `existing-sufficient`, or `not-needed`; explicitly read any
  active project instruction file before proceeding.
- Re-run `project-agent-instructions` when its decision is absent or when the
  requirements, design, inherited instruction chain, selected project,
  evidence, renderer, target, or prior decision fingerprint changes. Treat its
  structured conflict or safety blocker as the current blocked state rather
  than bypassing it.
- Build or refresh the feature queue from `docs/design.md`.
- Check `STEERING.md` and `steering/auto-steering.json` before every
  iteration.
- If there are new or unresolved steering entries, stale steering fingerprints,
  conflicting reminders, or changed requirements, design, context, plan, or
  evidence reminders, route to `sdlc-auto-steering` before selecting the next
  implementation phase.
- Honor `sdlc-auto-steering` dispositions: route `requirements-change` to
  `sdlc-create-requirements`, `design-change` to `sdlc-create-design`,
  `project-agent-instructions-change` to `project-agent-instructions`,
  `docs-update` to `sdlc-update-documents`, and `needs-human` to a blocked
  human-input state.
- Select the highest-priority incomplete feature whose dependencies are complete.
- Keep exactly one feature execution active. After `plan_locked`, route to
  `sdlc-prepare-execution`; after `execution_prepared`, route to `sdlc-tdd` in
  the recorded integration cwd. From TDD through alignment, require phases to
  use that integration checkout and bind evidence to its recorded HEAD.
- During implementation, route repeatedly to `sdlc-implement-plan` until every
  dependency wave is integrated, combined evidence passes, and worker cleanup
  completes. Task agents may run concurrently only inside the current wave.
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
- After evaluation passes, route to `sdlc-update-documents` before
  `sdlc-align-specs` when feature-facing docs, changelog, examples, or
  documentation steering need updates.
- After UAT, route back to `sdlc-update-documents` before PR creation when UAT
  or final steering requires run-level documentation updates.
- In a managed outer worktree, invoke the private `release-outer-lease`
  transition only after final alignment, UAT, and documentation evidence are
  recorded, the exact promoted HEAD is clean, and all Agentic SDLC integration
  and worker resources are absent. Route to `create-pr` only after release.
- Treat the shared PR skills as restricted SDLC modes after that handoff:
  `create-pr` may publish only the clean exact promoted SHA, and `review-pr`
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
  required.

## Idempotency

- Every invocation starts by reading the latest checkpoint and current state.
- Every `run` starts with prompt intake. An unchanged digest reuses the current
  revision; a changed digest creates exactly one adjacent immutable revision.
- An unchanged completed prompt returns `ALREADY_COMPLETE`; editing that prompt
  starts a new run without changing completed history.
- Repeating the same invocation with unchanged specs, fingerprints, evidence,
  and steering returns the same current feature and `next_recommended_skill`.
- Repeating with unchanged auto-steering state must not re-route to
  `sdlc-auto-steering` or duplicate steering history.
- Repeating with a verified unchanged project-instruction decision reuses its
  private state and must not rewrite the project file.
- Completed features with unchanged fingerprints are skipped.
- Changed requirements or design invalidate only affected features and
  supersede stale plans; they also invalidate the project-instruction decision
  before planning resumes. Locked plan files are never edited in place.
- If execution is already prepared, route requirement/design/plan changes
  through a new locked plan version and private `replan-future`. Replace only
  resource-free planned waves; do not reset or rewrite active/completed
  assignments, commits, worktrees, or evidence.
- History entries, checkpoints, and blocker summaries are append-only except
  for `checkpoints/latest.json` and `current-state.json` pointers.
- Partial writes are repaired by selecting the newest complete checkpoint and
  regenerating derived pointers.

## Failure Handling

- If state is corrupt, reconstruct from the newest complete checkpoint, specs,
  commits, and evidence when possible.
- If an unfinished active run has no managed prompt binding, stop with
  `WORKFLOW_UPGRADE_REQUIRED`. Completed unbound history remains readable; do
  not adopt or migrate it.
- If plan lock conflicts exist, stop and report.
- If any execution coordinator schema v1, v2, or v3 record exists, stop with
  `WORKFLOW_UPGRADE_REQUIRED`, including for completed records. If registered Git identity, plan digest, or
  exact HEAD drifts, stop with the execution-plane failure code rather than
  routing around the coordinator.
- If a feature loops repeatedly, classify the blocker and stop after retry budget.
- If a first direct repair fails with the same blocker, require
  `troubleshoot` before another remediation dispatch. Every retry requires
  newly acquired evidence and a distinct falsifiable hypothesis.
- Do not infer design from an unresolved diagnosis. Stop with the precise
  missing evidence or competing hypotheses.
- If no checkpoint can be trusted, stop with `HUMAN_INPUT_REQUIRED` instead of
  guessing the phase.

## Must Not

- Free-edit requirements or design.
- Create, overwrite, or delete project instruction files directly; route their
  conditional ownership to `project-agent-instructions`.
- Implement code directly.
- Commit, push, create PRs, review PRs, or merge.
- Bypass validation, tests, or evaluation.
- Route directly from `troubleshoot` to implementation or design.
- Reset repair counters without causally independent blocker evidence or a new
  explicit user-authorized tranche.
- Record live environment credentials, private endpoints, customer data, or raw
  logs in run state.
- Persist prompt content containing detected secret material.
- Create a public workflow CLI. Private helpers may perform deterministic
  workspace, prompt metadata, intake, verification, disposition, recovery,
  replanning, dispatch, and lease transitions only.

## Completion Criteria

- Active run state is accurate and backed by a checkpoint.
- The run is bound to one validated managed prompt and immutable revision.
- Current feature and next skill are explicit.
- Active repair state, owner, invalidations, budgets, and stop reason are
  explicit and checkpointed when a failure cycle exists.
- Steering has been consumed, refreshed, or routed to `sdlc-auto-steering`.
- The current project-instruction decision is verified and its outcome and
  fingerprint are checkpointed.
- Each state transition writes a checkpoint and history entry.
- Repeated resumes without state changes do not duplicate history.
- The loop can resume after context loss.

## SDLC Invariants

- Treat `docs/requirements.md`, `docs/design.md`, and any
  provenance-owned project-root `AGENTS.md` as committed project truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only `sdlc-create-design`
  writes `docs/design.md`; only `project-agent-instructions` may create or
  refresh its generated project-root `AGENTS.md`. Other skills route changes
  to those owners.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- Keep editable prompts and project-level prompt workspace metadata under the
  matching private `~/.codex/sdlc-runs/<project-id>/` directory.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different SDLC shape.
- Parallelism is task-local within one feature: one fresh agent, branch, and
  private worktree per safe task, with the coordinator as the only integration
  and cleanup owner.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.
- Keep optional SDLC hook source under `assets/hooks/`. Patch that source first,
  validate it with its local tests, then intentionally sync it to `$CODEX_HOME/hooks`
  with `install-skills.sh --install-all-hooks`, or with
  `install-skills.sh --install-hooks sdlc-start/assets/hooks` for an SDLC-only
  hook sync. Add `--register-hooks` only when the operator explicitly wants the
  installer to merge the SDLC `PreToolUse` and `Stop` registrations into
  `$CODEX_HOME/hooks.json`.

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

- Read `references/prompt-workspace.md` for prompt workspace, intake, revision,
  steering-linkage, and legacy-run rules.
- Read `references/state-schema.md` when this skill needs that local policy or schema.
- Read `assets/hooks/README.md` when maintaining or installing optional SDLC hooks.
