# Task Implementer

`task-implementer` is an explicit-only brownfield implementation coordinator.
It keeps prompts, revisions, steering, orchestration state, assignments,
results, and Git worktrees outside the repository while product changes remain
normal reviewed commits.

## Two-Command Workflow

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
```

Initialization creates or verifies the private `CODE` + `PROMPTS` workspace
and one starter prompt when needed. Edit that prompt, then invoke `run` once.
The run continues every dependency wave until completion or a precise blocker;
users never supply run, wave, task, branch, or worktree IDs.

Steer active work by editing the same prompt and repeating `run`. Steering
received after a wave starts remains queued until the next safe boundary. An
unchanged completed prompt returns `ALREADY_COMPLETE`; an edited completed
prompt starts a new run.

## Dependency Waves

Before implementation, the coordinator inspects source and locks stable tasks
with dependencies, exact or directory-prefix write claims, keyed conflict
domains, validation, and done criteria. It builds deterministic earliest-fit
waves in stable task order.

Tasks may share a wave only when dependencies are already satisfied and their
ownership is completely disjoint. Shared interfaces, schemas, migration
chains, dependency files, abstractions, Kubernetes/Terraform identities,
exclusive test resources, external mutations, and architecture decisions
serialize. Unknown ownership forces a singleton wave.

Logical waves may exceed runtime agent capacity. They dispatch in stable
capacity-sized batches without changing the dependency wave.

## Worktrees And Workers

Every parallel-capable task receives a unique branch and full-repository linked
worktree under:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/worktrees/
<project>/<scope>/<run>/wave-001/{integration,task-1,task-2}
```

For a monorepo scope such as `services/nebius-cxcli`, the worker cwd is
`<task-worktree>/services/nebius-cxcli`. Worktrees share Git objects, refs,
configuration, and hooks but have separate indexes and working files.

The main thread is coordinator-only. It dispatches native workers up to
capacity, or fresh sequential `codex exec` workers when native subagents are
unavailable. Each worker verifies its immutable assignment, implements exactly
one task inside locked claims, validates, runs `code-review`, fixes scoped
findings, and creates exactly one direct-child commit through `$commit`.

Workers never edit the shared handoff, managed specs, common docs, other refs
or worktrees, or the primary checkout. An undeclared path requirement stops
with `REPLAN_REQUIRED` before edit or commit.

## Integration And Promotion

The coordinator verifies worker Git evidence independently, then merges task
branches into a temporary integration branch in stable task-ID order with
`git merge --no-ff --no-edit`. Shared managed specs, README/design docs, and
changelog remain coordinator-owned.

After combined validation, integration `code-review`, and steering
reconciliation, the unchanged clean primary branch advances atomically with
`git merge --ff-only <verified-integration-SHA>` after the integration branch
is verified at that SHA. This fast-forward promotion is the only point where
tasks become done.

Clean reachable worktrees are then removed without force and ancestry-proven
branches are deleted with `git branch -d`. Failures preserve exact resources
for recovery. The workflow never runs broad prune/gc, cherry-picks, rebases,
squashes, pushes, or force-removes.

## Private State And Recovery

Coordinator, wave, mutable task-plane, immutable assignment/result, and journal records live with the run
under the private prompt workspace. Every Git mutation is journaled before
execution and re-observed afterward. A repeated `run` resumes durable v2 truth
without recreating branches, worktrees, assignments, commits, or merges.

Unfinished execution-plane-v1 runs are inert and return
`WORKFLOW_UPGRADE_REQUIRED`; completed v1 history remains readable. There is no
compatibility execution path or migration command.

Prompt filenames stay stable. Submission order comes from private
`last_invoked_at`, not filenames or mtimes. Output never prints prompt bodies,
secrets, or internal IDs.

## Files

- `SKILL.md`: explicit two-command coordinator contract.
- `agents/openai.yaml`: UI metadata and explicit-only policy.
- `references/prompt-workspace.md`: private storage, routing, v2 state, errors,
  and sandbox behavior.
- `references/implementation-loop.md`: task analysis, wave lifecycle, worker,
  integration, promotion, cleanup, and recovery rules.
- `assets/handoff-template.md`: coordinator-owned queue and wave evidence.
- `scripts/prompt_workspace_execution.py`: parsing and deterministic scheduler.
- `scripts/prompt_workspace_waves.py`: journaled worktree lifecycle.
- `scripts/prompt_workspace_intake.py`: two-command routing and steering.
- `scripts/prompt_workspace_specs.py`: managed specification validation.
- `scripts/test-task-execution.py`: scheduler and v1-boundary tests.
- `scripts/test-task-waves.py`: disposable real-Git lifecycle tests.
- `scripts/test-prompt-workspace.py`, `test-task-specs.py`, and
  `test-task-implementer-contract.py`: storage, spec, and contract tests.

## Boundaries

- Explicit invocation only; generic parallel requests do not trigger it.
- Public surface remains exactly `workspace init` and `run`.
- External database, Kubernetes, Terraform, migration, and publication actions
  remain singleton and need separate explicit authority.
- Use `$align` after the final promoted wave; use `$sdlc-start` for Agentic
  SDLC.
