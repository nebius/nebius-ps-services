# Agentic SDLC State Schema

Private state belongs to `<agent-home>/sdlc-runs/<project-id>/<run-id>/`.
The shared schemas and mutation helpers are defined in
`../../worktree/references/workflow-contract.md`.

## Objective and Execution Layout

```text
sdlc-runs/
  bindings/<native-session-sha256>.json
  <project-id>/
    project.json
    active-run.json
    active.lock
    <run-id>/
      run.json
      objective.json
      objectives/r0001.json
      clarifications.json
      requirements-refinement.json
      objective-impact-claim.json
      objective-impact/attempt-0001.json
      objective-impact/ledger.json
      objective-impact/execution/FEAT-001.json
      bases/FEAT-001/<sha256>.json
      current-state.json
      feature-queue.json
      fingerprints.json
      STEERING.md
      steering/auto-steering.json
      checkpoints/checkpoint-0001.json
      checkpoints/latest.json
      context/FEAT-001.context.md
      plans/FEAT-001.plan.v1.md
      plans/FEAT-001.plan.v1.md.lock
      execution/FEAT-001/
        base.json
        coordinator.json
        waves/WAVE-001.json
        tasks/WAVE-001/TASK-001.json
        assignments/WAVE-001/TASK-001.json
        incoming-handoffs/WAVE-001/TASK-001.json
        results/WAVE-001/TASK-001.json
        terminations/TASK-001.json
        admissions/TASK-001.json
        reconciliations/WAVE-001-TASK-001.json
        sessions/<session-sha256>.json
        journals/coordinator.jsonl
      worktrees/FEAT-001/integration/
      worktrees/FEAT-001/waves/WAVE-001/TASK-001/
      repairs/FEAT-001/
        repair-control.json
        repair-journal.jsonl
        events/<event-id>.json
        diagnoses/<diagnosis-id>.json
        approvals/<approval-id>.json
        classifications/<classification-id>.json
        dispatches/<dispatch-id>.json
        revalidations/<revalidation-id>.json
      evidence/FEAT-001/
      evidence/uat/
      permissions/
      history/iteration-0001.md
```

No prompt workspace, raw-turn archive or unrelated-objective queue exists.
`objective.json` selects ordered immutable normalized revisions and ancestry.
`run.json` uses `workflow/run-v1`; phase fields live in `current-state.json`.
The active pointer selects only a project run. It grants no session ownership.
`active.lock` uses `workflow/coordinator-ownership-v1` and must agree with the
run, project, native coordinator hash and `workflow/session-binding-v1` record.
Only the coordinator can create or replace that binding. Session observation is
read-only and never starts work. A new session uses explicit `continue` after
writer quiescence. Obsolete schemas fail without conversion or deletion.

Execution coordinators use `workflow/execution-coordinator-v1`, state version 1.
Waves use ready, active and admitted task IDs with one frozen base and a full-wave
integration barrier. Assignments and results bind immutable basis and plan
identities. Termination permits slot reuse only after independent coordinator
observation and result admission; a result file alone is insufficient.

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
    "align": 0,
    "sdlc-commit": 0,
    "uat": 0,
    "create-pr": 0,
    "review-pr": 0,
    "sdlc-merge-pr": 0
  },
  "last_successful_phase": "sdlc-create-plan",
  "next_recommended_skill": "sdlc-prepare-execution",
  "project_spec_observation": {
    "status": "current|pending|invalid|migration-required",
    "observed_at": "<RFC3339 timestamp>",
    "message": "<sanitized advisory>"
  },
  "repair": null,
  "execution": {
    "schema": "workflow/execution-coordinator-v1",
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

An environment-only block after completed waves uses the failure owner's
`record-environment-recovery` transition instead of a code-remediation cursor.
Its `environment_recovery_id` binds the immutable original event, classification,
unchanged clean integrated commit and fingerprints, and passing original gate
evidence. The sole resolved invalidation remains in that immutable receipt,
while the pending projection becomes empty. Other pending invalidations reject
recovery. It preserves budgets. This exception cannot
clear a code-repair cursor, a policy/human block, or a failed verifier trial.

Shared `workflow/execution-coordinator-v1` binds the selected folder through
`git_root`, `selected_project_root`, `project_scope` and `scope_cwd`.
`workflow/execution-wave-v1` owns ordered ready, active and admitted task IDs;
there is no batch cursor. Shared task/assignment/result v1 records carry the
immutable basis digest, native session history, attempt, direct heartbeat,
claims, structured evidence and specification gaps. The coordinator records
exact termination before result admission or slot reuse. Recovery needs an
immutable coordinator authorization for the fresh native session and exact attempt.

`execution/<feature>/sessions/<hash>.json` prevents concurrent identity reuse.
Private transition locks and assignment-bound runtime leases synchronize writers.
`execution/interop.json` uses `workflow/worktree-interop-v1` for an ordinary managed
outer lease and source-integration handoff. Every retired coordinator or lane
format fails with `WORKFLOW_UPGRADE_REQUIRED`, preserving history and resources.

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
    "align": 0,
    "sdlc-commit": 0,
    "uat": 0,
    "create-pr": 0,
    "review-pr": 0,
    "sdlc-merge-pr": 0
  },
  "last_successful_phase": "sdlc-create-plan",
  "next_recommended_skill": "sdlc-prepare-execution",
  "project_spec_observation": {
    "status": "current|pending|invalid|migration-required",
    "observed_at": "<RFC3339 timestamp>",
    "message": "<sanitized advisory>"
  },
  "repair": null,
  "fingerprint_ids": [
    "requirements:sha256:<digest>",
    "design:sha256:<digest>"
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

The `execution` member above is an informational checkpoint projection. The
execution owner's authoritative state is `execution/<FEAT-ID>/coordinator.json`,
using `workflow/execution-coordinator-v1` with `state_version: 1`. Retired
`agentic-sdlc/execution-coordinator-v1` through
`agentic-sdlc/execution-coordinator-v7` records stop with
`WORKFLOW_UPGRADE_REQUIRED`; do not create a compatibility path or mutate their
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

1. Observe the same session binding, or use explicit cross-session `continue`.
2. Validate accepted objective ancestry, the current checkpoint, exact source and
   registered execution resources. Read phase state only from that selected run.
3. Reconcile current state, checkpoint, feature queue and evidence; preserve
   repair budgets and prior attempts. Resume the newest complete checkpoint whose
   evidence identities still match.
4. Material steering first fences progress. Observe termination and reconcile
   affected writers, then publish current spec impact and a distinct plan where
   required. Resume only after every active execution basis is current.
5. After promotion, use the delivered checkout. Documentation drift starts a
   fresh corrective run; do not reopen completed execution.
6. Old or foreign state remains untouched and fails `WORKFLOW_UPGRADE_REQUIRED`.
   Missing native session binding does not authorize scanning for a sole run.

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
4. sdlc-auto-steering
5. plan
6. execution preparation
7. sdlc-tdd, in the integration worktree
8. implementation dependency waves, in worker and integration worktrees
9. validation, at the recorded integration HEAD
10. test, at the recorded integration HEAD
11. evaluation, at the recorded integration HEAD
12. sdlc-update-documents, in the integration worktree
13. general align quality gate, in the integration worktree
14. sdlc-commit: final seal, ff-only promotion, and integration cleanup
15. uat, from the promoted project checkout
16. managed child only: outer-integration-pending, then return the recorded
    primary path and await a fresh explicit user invocation of
    `$worktree integrate` from that primary checkout
17. create-pr, from the final unmanaged source branch only
18. review-pr
19. sdlc-merge-pr, only after explicit user request

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

After UAT, `sdlc-update-documents` may assess the promoted checkout read-only
and record no-op evidence. Required edits return `DOCUMENTATION_DRIFT` through
classification into a corrective run from the exact promoted commit; completed
execution never reopens. Fresh gates and UAT precede handoff. In a managed child, lease release enters
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
It is the active-run inbox and ledger for accepted objective revisions,
including runtime instructions, requirements-related notes,
design-related notes, project-instruction updates, documentation updates,
blockers, priority overrides,
pause instructions, and UAT rerun requests.

Each inbox entry links the accepted objective ID, revision, SHA-256 digest, and
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
instructions route through `project-agent-instructions` only when the current
user explicitly requested the separate mutation workflow. Otherwise the entry
is recorded as advisory and does not block SDLC progress.

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

The Stop hook continuation prompt must route through explicit `$sdlc-start continue [project-folder]` invocation and emit canonical `sdlc-*` skill names. It
does not select or continue a run without a valid native-session/objective binding;
explicit intake rejects unsupported state. It may normalize short
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
