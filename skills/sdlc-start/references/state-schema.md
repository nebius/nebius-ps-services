# Agentic SDLC State Schema

Use this reference when creating, repairing, or reading local SDLC run state.
All run artifacts are private local state under
`~/.codex/sdlc-runs/<project-id>/<run-id>/` and must not be committed.

## Layout

```text
~/.codex/sdlc-runs/
  <project-id>/
    active-run.json
    active.lock
    <run-id>/
      run.json
      current-state.json
      feature-queue.json
      fingerprints.json
      STEERING.md
      steering/
        auto-steering.json
      permissions/
        commit-authorization.json
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

`active-run.json` is a small project-level pointer to the active run ID. It is
the only project-level file that should change when switching active runs.

## Minimum current-state.json

```json
{
  "state_version": 1,
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
    "sdlc-tdd": 0,
    "implementation": 0,
    "validation": 0,
    "test": 0,
    "evaluation": 0,
    "sdlc-update-documents": 0,
    "sdlc-align-specs": 0,
    "sdlc-commit": 0,
    "uat": 0,
    "create-pr": 0,
    "review-pr": 0,
    "sdlc-merge-pr": 0
  },
  "last_successful_phase": "sdlc-create-plan",
  "next_recommended_skill": "sdlc-tdd",
  "updated_at": "2026-06-16T00:00:00Z"
}
```

## Minimum checkpoint

Each checkpoint is an append-only JSON snapshot that lets `sdlc-start` resume
without conversation history.

```json
{
  "state_version": 1,
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
    "sdlc-tdd": 0,
    "implementation": 0,
    "validation": 0,
    "test": 0,
    "evaluation": 0,
    "sdlc-update-documents": 0,
    "sdlc-align-specs": 0,
    "sdlc-commit": 0,
    "uat": 0,
    "create-pr": 0,
    "review-pr": 0,
    "sdlc-merge-pr": 0
  },
  "last_successful_phase": "sdlc-create-plan",
  "next_recommended_skill": "sdlc-tdd",
  "fingerprint_ids": [
    "requirements:sha256:<digest>",
    "design:sha256:<digest>"
  ],
  "evidence": {
    "context": "context/FEAT-001.context.md",
    "plan": "plans/FEAT-001.plan.v1.md",
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

`checkpoints/latest.json` points to the newest complete checkpoint:

```json
{
  "checkpoint_id": "checkpoint-0007",
  "path": "checkpoints/checkpoint-0007.json",
  "created_at": "2026-06-16T00:00:00Z"
}
```

## Resume Rules

1. Read `active-run.json`, `current-state.json`, `checkpoints/latest.json`, and
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

## Hook Authorization Files

Short-lived authorization files live under `permissions/` in the active run
directory. They are local runtime state only and must not be committed.

- `commit-authorization.json`: written immediately before `sdlc-commit` runs
  `git commit`, after validation, tests, and evaluation evidence passes.
- `pr-authorization.json`: written immediately before `create-pr` publishes a
  branch or opens/reuses a PR, after UAT evidence passes unless the user
  explicitly requested an early draft PR.
- `merge-authorization.json`: written immediately before `sdlc-merge-pr` merges a
  specific PR, after the explicit user merge request and final readiness checks.

Each file must include `allowed: true`, an `expires_at` timestamp, and the
branch or PR scope it authorizes. Sensitive actions should remove or expire the
file after use. The PreToolUse hook treats missing, expired, corrupt, or
wrong-scope authorization as a policy block.

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
6. sdlc-tdd
7. implementation
8. validation
9. test
10. evaluation
11. sdlc-update-documents
12. sdlc-align-specs
13. sdlc-commit
14. uat
15. create-pr
16. review-pr
17. sdlc-merge-pr, only after explicit user request

After UAT, `sdlc-start` may route back to `sdlc-update-documents` in run scope
before `create-pr` when UAT or final steering changes require project-facing
documentation updates.

## Steering

`STEERING.md` is temporary runtime steering, not a second requirements file.
It is the active-run inbox and ledger for user prompts submitted during an
active SDLC run, including runtime instructions, requirements-related notes,
design-related notes, documentation updates, blockers, priority overrides,
pause instructions, and UAT rerun requests.

`sdlc-auto-steering` owns refreshing `STEERING.md` and
`steering/auto-steering.json`. Each unresolved entry must have one disposition:
`runtime-only`, `requirements-change`, `design-change`, `docs-update`,
`resolved`, `superseded`, `rejected`, or `needs-human`. Raw secrets,
credentials, private endpoints, customer data, and noisy logs must be redacted
before they are persisted.

Only compact active reminders and unresolved routing decisions should be
injected into the agent loop. Durable product changes still route through
`sdlc-create-requirements` and `sdlc-create-design`; documentation-only changes
route through `sdlc-update-documents`.

## Hook Boundary

A Stop hook may continue a run by injecting a continuation prompt. A PreToolUse
hook may deny unsafe actions. Hooks enforce invariants only; `sdlc-start` owns
workflow decisions.

Optional Agentic SDLC hook source lives under
`sdlc-start/assets/hooks/`. Installed copies under `$CODEX_HOME/hooks` are
runtime artifacts and can drift, so maintainers should patch the source bundle
first, run its hook tests, and then sync it intentionally with
`install-skills.sh --install-all-hooks`, or with
`install-skills.sh --install-hooks sdlc-start/assets/hooks` when only the SDLC
hook bundle should be refreshed. Add `--register-hooks` when the installer
should also merge the SDLC `PreToolUse` and `Stop` registration entries into
`$CODEX_HOME/hooks.json`.

The Stop hook continuation prompt must route through explicit `$sdlc-start`
invocation and emit canonical `sdlc-*` skill names. It may normalize short
phase values when reading local state, but those aliases must not appear as
emitted next-skill names. Steering text that pauses work or controls PR
creation, including `Pause after the current feature. Do not create a PR.`,
must be treated as coordinator input rather than ignored. Broader steering
classification belongs to `sdlc-auto-steering`, not the hook.

The PreToolUse hook does not block filesystem targets by path. Repository
files, outside-repo files, credential directories, Codex runtime files, global
`AGENTS.md`, locked SDLC plans, and private SDLC state are path-allowed for
operator flexibility. It must still block secret-bearing payloads, dangerous
shell patterns, and guarded Git or GitHub actions without valid short-lived
authorization. Use the explicit hook installer option for deliberate runtime
sync instead of editing installed hook artifacts directly.
