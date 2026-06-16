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
          screenshots/
          transcripts/
          failure-log.md
          commit.md
        uat/
          uat-report.md
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
    "plan": 0,
    "sdlc-tdd": 0,
    "implementation": 0,
    "validation": 0,
    "test": 0,
    "evaluation": 0,
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
    "plan": 0,
    "sdlc-tdd": 0,
    "implementation": 0,
    "validation": 0,
    "test": 0,
    "evaluation": 0,
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
4. plan
5. sdlc-tdd
6. implementation
7. validation
8. test
9. evaluation
10. sdlc-align-specs
11. sdlc-commit
12. uat
13. create-pr
14. review-pr
15. sdlc-merge-pr, only after explicit user request

## Steering

`STEERING.md` is temporary runtime steering, not a second requirements file.
Use it for urgent corrections, priority overrides, blocker answers, pause
instructions, or UAT rerun requests. Convert durable product changes through
`sdlc-create-requirements` and `sdlc-create-design`.

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
hook bundle should be refreshed.

The Stop hook continuation prompt must route through `sdlc-start` and emit
canonical `sdlc-*` skill names. It may normalize short phase values when
reading local state, but those aliases must not appear as emitted next-skill
names. Steering text that pauses work or controls PR creation, including
`Pause after the current feature. Do not create a PR.`, must be treated as
coordinator input rather than ignored.

The PreToolUse hook must continue blocking out-of-scope writes such as direct
runtime hook edits from an unrelated project workspace. Use the explicit hook
installer option for deliberate runtime sync instead of broadening the write
allowlist.
