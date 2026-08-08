# Agentic SDLC State Schema

Use this reference when creating, repairing, or reading local SDLC run state.
All run artifacts are private local state under
`~/.codex/sdlc-runs/<project-id>/<run-id>/` and must not be committed.

## Layout

```text
~/.codex/sdlc-runs/
  <project-id>/
    workspace.json
    activity.json
    prompt-queue.json
    queued-prompts/
    prompt.lock
    prompts/
      00-START-HERE.md
      <created-at>--<slug>.md
    <project>-prompts.code-workspace
    active-run.json
    active.lock
    <run-id>/
      prompt.json
      requirements-refinement.json
      inputs/
        r0001/prompt.md
      run.json
      current-state.json
      feature-queue.json
      fingerprints.json
      STEERING.md
      steering/
        auto-steering.json
      permissions/
        commit-authorization.json
        execution/
          <action-id>.json
        pr-authorization.json
        merge-authorization.json
      checkpoints/
        checkpoint-0001.json
        latest.json
      context/
        FEAT-001.context.md
      plans/
        FEAT-001.plan.v1.md
        FEAT-001.plan.v1.lock
      execution/
        FEAT-001/
          coordinator.json
          waves/WAVE-001.json
          tasks/WAVE-001/TASK-001.json
          assignments/WAVE-001/TASK-001.json
          incoming-handoffs/WAVE-001/TASK-001.json
          sessions/<session-hash>.json
          results/WAVE-001/TASK-001.json
          journals/coordinator.jsonl
      repairs/
        FEAT-001/
          repair-control.json
          repair-journal.jsonl
          events/<event-id>.json
          diagnoses/<diagnosis-id>.json
          approvals/<approval-id>.json
          classifications/<classification-id>.json
          revalidations/<revalidation-id>.json
      worktrees/
        FEAT-001/integration/
        FEAT-001/waves/WAVE-001/TASK-001/
      evidence/
        FEAT-001/
          validate.md
          tests.md
          evaluate.md
          documents.md
          screenshots/
          transcripts/
          failure-log.md
          commit.md
        uat/
          uat-report.md
          documents.md
        pr.md
        review.md
        merge.md
      history/
        iteration-0001.md
```

`workspace.json` binds the exact canonical project folder to the prompt
workspace. `activity.json` records last invocation timestamps without touching
editable prompt mtimes. `active-run.json` is a small project-level pointer to
the active run ID. Read `prompt-workspace.md` for the prompt and revision
schemas.

Each new managed run has `prompt.json` with schema
`agentic-sdlc/prompt-binding-v2`, one stable prompt ID and filename, lineage
root, optional terminal predecessor, and an ordered revision list. Each
revision records `rNNNN`, accepted timestamp, raw and normalized-intent SHA-256
digests, revision kind, immutable snapshot pointer, and steering status. The run's
`run.json` must mirror the bound prompt ID, filename, latest accepted revision,
digests, kind, and snapshot pointer so hooks can continue without reading prompt
bodies.

`requirements-refinement.json` uses
`agentic-sdlc/requirements-refinement-v1`. It binds the latest accepted intent
to categorized extraction, stable `Q-*` clarification state and provenance,
the compiled requirements digest, and `extracting`, `needs_clarification`, or
`ready` status. Material open or reopened questions prevent `ready`.

`prompt-queue.json` is a private FIFO of explicit run requests for prompts that
cannot yet become active. Its immutable accepted snapshots live under
`queued-prompts/`. Creation and saving never add entries; an edited queued
prompt changes its entry only after another explicit run. Queue-head drift
blocks activation, and the coordinator activates the head only after the
active run and execution resources are terminal and released.

## Minimum current-state.json

```json
{
  "state_version": 2,
  "project_id": "<project-id>",
  "run_id": "<run-id>",
  "iteration": 7,
  "checkpoint_id": "checkpoint-0007",
  "current_feature": "FEAT-001",
  "current_phase": "plan_locked",
  "status": "running",
  "blocked_reason": null,
  "needs_human": false,
  "iteration_count": 7,
  "max_iterations": 200,
  "retry_counts": {
    "requirements": 0,
    "context": 0,
    "design": 0,
    "project-agent-instructions": 0,
    "sdlc-auto-steering": 0,
    "plan": 0,
    "execution_preparation": 0,
    "sdlc-tdd": 0,
    "implementation": 0,
    "validation": 0,
    "test": 0,
    "evaluation": 0,
    "failure_classification": 0,
    "troubleshoot": 0,
    "sdlc-update-documents": 0,
    "sdlc-align-specs": 0,
    "sdlc-commit": 0,
    "uat": 0,
    "create-pr": 0,
    "review-pr": 0,
    "sdlc-merge-pr": 0
  },
  "last_successful_phase": "sdlc-create-plan",
  "next_recommended_skill": "sdlc-prepare-execution",
  "project_agent_instructions": {
    "outcome": "not-needed",
    "decision_fingerprint": "<sha256>",
    "spec_receipt": "project-agent-instructions/spec-receipt.json",
    "ownership_receipt": "project-agent-instructions/ownership.json",
    "state": "project-agent-instructions/state.json",
    "active_instruction": null,
    "effective_config_fingerprint": "<sha256>",
    "reload_required": false
  },
  "repair": null,
  "execution": {
    "schema": "agentic-sdlc/execution-coordinator-v7",
    "feature_id": "FEAT-001",
    "status": "not_prepared",
    "coordinator": "execution/FEAT-001/coordinator.json",
    "git_root": "/absolute/git/root",
    "default_remote": "origin",
    "default_branch": "trunk",
    "default_ref": "origin/trunk",
    "default_head": "<sha>",
    "base_branch": "feature/sdlc-<run-hash>",
    "project_scope": "services/example",
    "integration_worktree": null,
    "integration_branch": null,
    "integration_head": null,
    "active_wave": null
  },
  "updated_at": "2026-06-16T00:00:00Z"
}
```

When a repair cycle is active, `repair` is a pointer projection rather than a
second mutable budget:

```json
{
  "schema": "agentic-sdlc/repair-state-pointer-v1",
  "failure_event": "repairs/FEAT-001/events/<event-id>.json",
  "diagnosis": "repairs/FEAT-001/diagnoses/<diagnosis-id>.json",
  "control": "repairs/FEAT-001/repair-control.json"
}
```

`diagnosis` is `null` until causal diagnosis exists. The immutable
`failure-event-v1`, optional immutable `diagnosis-v1`, optional immutable
`design-approval-v1`, deterministic classification, append-only repair
journal, and process-locked atomic `repair-control-v1` projection are
authoritative. A broader-design approval also binds the digest and exact
approval excerpt of its private user-input snapshot. `event_id` identifies exact commit-bound
evidence; `blocker_key` identifies recurrence across commits and excludes
timestamps, temporary IDs, line numbers, checkpoints, and commits. The
human-readable `evidence/FEAT-*/failure-log.md` is derived and never
authoritative.

After a successful remediation, any non-empty invalidation set creates an
`agentic-sdlc/revalidation-cursor-v1`. It names the ordered surfaces and exact
next skill. Each phase advances it with immutable, digest-verified
`revalidation-evidence-v1` bound to current requirements/design/plan
fingerprints. Pre-commit evidence follows the live integration HEAD; final
commit evidence follows the promoted project HEAD on the recorded base branch
only after worktree and branch cleanup. The evidence source is a structured
phase-owned `passed` result; the cursor is content-identified and bound to the
immutable classification plus a completed successful dispatch. `resolved` is
valid only when the cursor is complete; the Stop hook rejects UAT, PR, or
publication routing while any invalidated gate remains.

Coordinator v7 binds the exact initialized folder through `git_root`,
`selected_project_root`, `project_scope`, and per-assignment `scope_cwd`.
Execution wave v2 enforces an active capacity-batch cursor. Mutable task v4
records store append-only hashed worker-session history, attempt count,
dispatch/start timestamps, heartbeat phase/sequence, and an optional
digest-protected `task-finish-intent-v1`; assignment v3 binds the exact
helper/run identity, liveness profile, and immutable `incoming-handoff-v1`.
Private `task-arm`, `task-start`, direct `task-heartbeat`, and read-only
`task-watch` transitions are Agentic-owned and never share Task Implementer
state. Private `task-requeue` returns only a confirmed-stopped, never-started,
clean exact dispatch to the queue. The coordinator-owned `task-finish` creates
the task commit after recording its assignment/base/tree/message/evidence
intent. Retry can adopt only that clean direct-child commit and an exact result
from either persistence crash window. A
`worker-result-v4` carries an explicit summary, decisions, open risks, and,
for corrective tasks, digest-protected `regression-oracle-evidence-v1` bound
to the exact diagnosis, oracle, and coordinator-created task commit. Atomic private
`execution/<feature>/sessions/<hash>.json` claims prevent concurrent reuse of
one session identity across tasks. A private execution transition lock and
expected-attempt recovery guard make each task ownership transfer exclusive.
Optional `execution/interop.json` uses
`agentic-sdlc/worktree-interop-v2` for a managed outer-worktree lease and
source-integration handoff. Every coordinator v1 through v6 record fails with
`WORKFLOW_UPGRADE_REQUIRED` and is
not mutated, including completed records.

## Minimum checkpoint

Each checkpoint is an append-only JSON snapshot that lets `sdlc-start` resume
without conversation history.

```json
{
  "state_version": 2,
  "checkpoint_id": "checkpoint-0007",
  "project_id": "<project-id>",
  "run_id": "<run-id>",
  "iteration": 7,
  "created_at": "2026-06-16T00:00:00Z",
  "current_feature": "FEAT-001",
  "current_phase": "plan_locked",
  "status": "running",
  "blocked_reason": null,
  "retry_counts": {
    "requirements": 0,
    "context": 0,
    "design": 0,
    "project-agent-instructions": 0,
    "sdlc-auto-steering": 0,
    "plan": 0,
    "execution_preparation": 0,
    "sdlc-tdd": 0,
    "implementation": 0,
    "validation": 0,
    "test": 0,
    "evaluation": 0,
    "failure_classification": 0,
    "troubleshoot": 0,
    "sdlc-update-documents": 0,
    "sdlc-align-specs": 0,
    "sdlc-commit": 0,
    "uat": 0,
    "create-pr": 0,
    "review-pr": 0,
    "sdlc-merge-pr": 0
  },
  "last_successful_phase": "sdlc-create-plan",
  "next_recommended_skill": "sdlc-prepare-execution",
  "project_agent_instructions": {
    "outcome": "not-needed",
    "decision_fingerprint": "<sha256>",
    "spec_receipt": "project-agent-instructions/spec-receipt.json",
    "ownership_receipt": "project-agent-instructions/ownership.json",
    "state": "project-agent-instructions/state.json",
    "active_instruction": null,
    "effective_config_fingerprint": "<sha256>",
    "reload_required": false
  },
  "repair": null,
  "fingerprint_ids": [
    "requirements:sha256:<digest>",
    "design:sha256:<digest>",
    "project-agent-instructions:sha256:<digest>"
  ],
  "evidence": {
    "context": "context/FEAT-001.context.md",
    "plan": "plans/FEAT-001.plan.v1.md",
    "execution": "execution/FEAT-001/coordinator.json",
    "validation": null,
    "tests": null,
    "evaluation": null,
    "documents": null,
    "uat": null,
    "pr": null,
    "review": null,
    "merge": null
  }
}
```

Execution coordinator v7 is the only supported execution schema. If an
`agentic-sdlc/execution-coordinator-v1` or
`agentic-sdlc/execution-coordinator-v2` or
`agentic-sdlc/execution-coordinator-v3` or
`agentic-sdlc/execution-coordinator-v4` or
`agentic-sdlc/execution-coordinator-v5` or
`agentic-sdlc/execution-coordinator-v6` record exists, stop with
`WORKFLOW_UPGRADE_REQUIRED`; do not create a compatibility path or mutate its
resources. This applies to completed records; there is no legacy read path.

`checkpoints/latest.json` points to the newest complete checkpoint:

```json
{
  "checkpoint_id": "checkpoint-0007",
  "path": "checkpoints/checkpoint-0007.json",
  "created_at": "2026-06-16T00:00:00Z"
}
```

## Resume Rules

1. Run private prompt intake, then read `active-run.json`, `prompt.json`,
   `current-state.json`, `checkpoints/latest.json`, and
   the latest checkpoint before selecting a phase.
2. Treat the latest complete checkpoint as authoritative when it matches
   available evidence.
3. If `current-state.json` points past the latest complete checkpoint, repair it
   from the checkpoint.
4. If a checkpoint exists but `checkpoints/latest.json` is stale, update only
   the pointer.
5. If the latest checkpoint and evidence disagree, use the newest earlier
   checkpoint that still matches evidence. If none can be trusted, stop with
   `HUMAN_INPUT_REQUIRED`.
6. Repeated runs with no state, steering, fingerprint, or evidence change must
   return the same `next_recommended_skill` without appending duplicate history.
7. An unfinished active run without `prompt.json` fails with
   `WORKFLOW_UPGRADE_REQUIRED`; completed unbound history stays readable.

## Hook Authorization Files

Short-lived authorization files live under `permissions/` in the active run
directory. They are local runtime state only and must not be committed.

- `commit-authorization.json`: written immediately before `sdlc-commit` runs
  `git commit`, after validation, tests, and evaluation evidence passes.
- `pr-authorization.json`: written immediately before `create-pr` publishes a
  branch or opens/reuses a PR, after UAT evidence passes for the clean exact
  SHA promoted by `sdlc-commit`. It must include `phase: "create-pr"`, the
  exact branch, `expected_head` equal to the recorded promoted HEAD,
  `base_branch` and `base_head` equal to the recorded remote-default identity,
  `uat_status: "passed"`, and a short expiry. A guarded Git push may contain
  only `origin` and one exact `HEAD:<branch>` refspec; CLI or MCP PR creation
  must name the same head branch. Shell execution must use one direct action
  without executable wrappers, absolute executable paths, prepended commands,
  shell operators, or redirections.
- `merge-authorization.json`: written immediately before `sdlc-merge-pr` merges a
  specific PR, after the explicit user merge request and final readiness
  checks. It binds `phase: "sdlc-merge-pr"`, the named branch and PR,
  `expected_head` equal to the promoted and reviewed SHA, the recorded
  remote-default `base_branch` and `base_head`, and the one
  canonical single-action `exact_command` containing an explicit PR number or
  URL and `--match-head-commit <expected_head>`, `explicit_user_request: true`,
  `checks_status: "passed"`, `review_status: "passed"`,
  `uat_status: "passed"`, and expiry. Only an optional long-form
  `--merge`/`--rebase`/`--squash` strategy is allowed; implicit PR selection,
  other flags, shell operators, and appended commands fail closed.
- `execution/<action-id>.json`: optional, single-action authorization for an
  operator-visible raw Git command inside a registered integration/worker
  worktree. It must scope the action (`worker-commit`, `integration-merge`,
  `resource-cleanup`, or `feature-promotion`), canonical worktree, branch, Git
  common directory, expected HEAD, expiry, and exact target when applicable.
  The private execution helper performs these transitions directly and does not
  require a standing broad authorization.

Each file must include `allowed: true`, an `expires_at` timestamp, and the
branch or PR scope it authorizes. Sensitive actions should remove or expire the
file after use. The PreToolUse hook treats missing, expired, corrupt,
wrong-scope, wrong-phase, or wrong-HEAD authorization as a policy block.

## Checkpoint Write Order

1. Build the next state in memory from specs, state, evidence, steering, and
   fingerprints.
2. Write the new `checkpoints/checkpoint-*.json` file completely.
3. Update `checkpoints/latest.json`.
4. Update `current-state.json`.
5. Append `history/iteration-*.md` only when the state changed.

Use atomic replace semantics where the local environment supports them. If a
write is interrupted, resume by selecting the newest complete checkpoint.

## Phase Order

1. requirements
2. context
3. design
4. project-agent-instructions
5. sdlc-auto-steering
6. plan
7. execution preparation
8. sdlc-tdd, in the integration worktree
9. implementation dependency waves, in worker and integration worktrees
10. validation, at the recorded integration HEAD
11. test, at the recorded integration HEAD
12. evaluation, at the recorded integration HEAD
13. sdlc-update-documents, in the integration worktree
14. sdlc-align-specs, in the integration worktree
15. sdlc-commit: final seal, ff-only promotion, and integration cleanup
16. uat, from the promoted project checkout
17. managed child only: outer-integration-pending, then return the recorded
    primary path and await a fresh explicit user invocation of
    `$worktree integrate` from that primary checkout
18. create-pr, from the final unmanaged source branch only
19. review-pr
20. sdlc-merge-pr, only after explicit user request

`troubleshoot` is a conditional diagnostic branch, not an additional
golden-path phase. A proven cause routes directly from
`sdlc-classify-failure` to its owning phase. An ambiguous, persistent,
intermittent, or cross-boundary failure routes once to `troubleshoot`, whose
`diagnosis-v1` must return to `sdlc-classify-failure` before any repair owner is
selected. Missing decisive evidence, competing hypotheses, policy, permission,
human decisions, and exhausted budgets stop the run.

The repair control enforces, per stable blocker tranche, at most two localized
repairs, one design-scale repair, three total repair attempts, and 60 active
minutes. It also enforces at most four repair dispatches per feature.
Diagnostic experiments consume time but not remediation attempts; three
low-information experiments require a rebuilt causal model. Exact duplicate
events, classification replays, permission denials, and crash recovery do not
consume attempts. Counters reset only for causally independent blocker
evidence or a new explicit user-authorized tranche.

`plan_locked` routes to `sdlc-prepare-execution`; `execution_prepared` routes to
`sdlc-tdd`. From preparation through final seal, the persistent integration
worktree is the canonical feature checkout. The original project branch must
remain clean and at `base_head` until `sdlc-commit` promotes the exact sealed
integration tip under the common-Git-directory lock with
`git merge --ff-only`.

After UAT, `sdlc-start` may route back to `sdlc-update-documents` in run scope
before final handoff when UAT or final steering changes require project-facing
documentation updates. In a managed child, lease release enters
`outer-integration-pending`; `sdlc-start` returns the recorded primary path plus
the exact `$worktree integrate <generated-name>` command and stops. Only a fresh
explicit user invocation from that primary checkout may run it. After that
separate action, the exact source merge proof must be recorded before the run is
complete. The child is never
published. In an unmanaged source checkout, `create-pr` is publication-only
for the clean exact promoted SHA and `review-pr` is
findings-and-readiness-only; branch-changing findings return through
`sdlc-classify-failure` and `sdlc-start`. `sdlc-merge-pr` then requires the
current PR head to remain that exact promoted and reviewed SHA.

## Steering

`STEERING.md` is temporary runtime steering, not a second requirements file.
It is the active-run inbox and ledger for accepted managed prompt revisions,
including runtime instructions, requirements-related notes,
design-related notes, project-instruction updates, documentation updates,
blockers, priority overrides,
pause instructions, and UAT rerun requests.

Each inbox entry links the managed prompt ID, revision, SHA-256 digest, and
snapshot pointer, but includes only a compact safe summary rather than the full
prompt body. `sdlc-auto-steering` owns refreshing `STEERING.md` and
`steering/auto-steering.json`. Each unresolved entry must have one disposition:
`runtime-only`, `requirements-change`, `design-change`,
`project-agent-instructions-change`, `docs-update`, `resolved`, `superseded`,
`rejected`, or `needs-human`. Raw secrets,
credentials, private endpoints, customer data, and noisy logs must be redacted
before they are persisted.

Only compact active reminders and unresolved routing decisions should be
injected into the agent loop. Durable product changes still route through
`sdlc-create-requirements` and `sdlc-create-design`; documentation-only changes
route through `sdlc-update-documents`; durable changes to generated project
instructions route through `project-agent-instructions`.

## Hook Boundary

A Stop hook may continue a run by injecting a continuation prompt. A PreToolUse
hook may deny unsafe actions. Hooks enforce invariants only; `sdlc-start` owns
workflow decisions.

Optional Agentic SDLC hook source lives under
`sdlc-start/assets/hooks/`. Installed copies under `$CODEX_HOME/hooks` are
runtime artifacts and can drift, so maintainers should patch the source bundle
first, run its hook tests, and then sync it intentionally with
`install-skills.sh --install-all-hooks --register-hooks
--refresh-hook-registrations`, or with
`install-skills.sh --install-hooks sdlc-start/assets/hooks --register-hooks
--refresh-hook-registrations` when only the SDLC hook bundle should be
refreshed. The refresh option replaces only a differing same-event,
same-script registration with the same handlers, allowing only `statusMessage`
metadata to differ while preserving unrelated entries in
`$CODEX_HOME/hooks.json`.

The Stop hook continuation prompt must route through explicit `$sdlc-start run
<bound-unique-filename>` invocation and emit canonical `sdlc-*` skill names. It
must stop instead of continuing an unfinished unbound legacy run. It may normalize short
phase values when reading local state, but those aliases must not appear as
emitted next-skill names. Steering text that pauses work or controls PR
creation, including `Pause after the current feature. Do not create a PR.`,
must be treated as coordinator input rather than ignored. Broader steering
classification belongs to `sdlc-auto-steering`, not the hook.

The PreToolUse hook does not block filesystem targets by path. Repository
files, outside-repo files, credential directories, Codex runtime files, global
`AGENTS.md`, locked SDLC plans, and private SDLC state are path-allowed for
operator flexibility. A user-level registration matches by tool name before
active-run discovery, so the hook immediately allows calls outside an active
SDLC run and its registration omits a static SDLC status message. During an
active run it must still block secret-bearing payloads, dangerous shell
patterns, and guarded Git or GitHub actions without valid short-lived
authorization. Use the explicit hook installer option for deliberate runtime
sync instead of editing installed hook artifacts directly.
