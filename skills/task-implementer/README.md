# Task Implementer

`task-implementer` is an explicit-only brownfield implementation coordinator.
It keeps prompts, revisions, steering, orchestration state, assignments,
results, and Git worktrees outside the repository while product changes remain
normal reviewed commits.

## Four-Action Workflow

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

Use `$task-implementer --help` or `$task-implementer -h` to display the
purpose, explicit invocation policy, these four workflow actions, their
arguments, and the help option itself. After the selected `SKILL.md` loads, Help
performs no workspace initialization, project inspection, additional tool
calls, or private-state changes; it is not a third workflow action and does not
authorize any lifecycle action.

Initialization creates or verifies the private `CODE` + `PROMPTS` workspace and
one starter prompt when needed, then asks VS Code to reuse its last active
window. Loading the workspace restarts that window's extension host and may
interrupt its terminal or Codex UI; editor failure remains non-fatal. Edit the
starter prompt, then invoke `run` once. The run continues every dependency wave
until completion or a precise blocker; users never supply run, wave, task,
branch, or worktree IDs.

Steer active work by editing the same prompt and repeating `run`. Steering
received after a wave starts remains queued until the next safe boundary. An
unchanged completed prompt returns `ALREADY_COMPLETE`; an edited completed
prompt starts a new run.

## Persistent Project Lanes

Run initialization from the exact monorepo project folder while the primary
checkout has configured `origin/HEAD` and is attached to a named non-default
source branch. Task Implementer
creates one persistent full-repository linked worktree, branch, and private VS
Code workspace for the exact common Git directory, primary checkout, source
ref, and repo-relative scope.

Initialization and runs may start while the source checkout is dirty. Only its
committed `HEAD` becomes the lane baseline; dirty and untracked source files are
never copied or mutated. The lane must remain clean at run boundaries. Separate
project scopes get separate lanes and may run concurrently when their
repository-wide exact/prefix claims and conflict domains do not overlap.

Before resources exist, replanning replaces the resource-free planned tail.
The coordinator index keeps completed waves plus the replacement schedule;
superseded planned wave files remain blocked history outside final completion.
The active lane generation reserves every newly introduced repository claim
before replacement state is written and retains earlier claims conservatively.
After a final promoted wave is cleaned, replanning can append an isolated
correction tail found by integration review before finalization.

Every `run` acquires the next monotonic generation at the lane's exact clean
`HEAD`. Finalization creates an immutable released-generation receipt and keeps
its repository claims pending. Back-to-back runs therefore accumulate a
contiguous pending range without touching the source checkout. `integrate`
validates and promotes the whole range, then fast-forwards and rearms the same
lane. `workspace remove` is explicit and proof-gated; it preserves private
prompts and run history, and a later initialization creates the next lane
incarnation and rebinds that history.

## Dependency Waves

Before implementation, the coordinator inspects source and locks stable tasks
with dependencies, exact or directory-prefix write claims, keyed conflict
domains, validation, and done criteria. It builds deterministic earliest-fit
waves in stable task order.

After the coordinator renders and validates Task Implementer-managed
requirements and design records, it routes to the explicit-only
`project-agent-instructions` skill. That shared owner creates a selected-project
`AGENTS.md` only when durable project-specific rules add to inherited
instructions. Human-owned files are preserved; only an unchanged
provenance-marked generated file can be refreshed. The receipt remains private,
while a created or refreshed file joins the coordinator contract commit before
worker dispatch. `workspace init` never creates project documentation or
instructions.

Tasks may share a wave only when dependencies are already satisfied and their
ownership is completely disjoint. Shared interfaces, schemas, migration
chains, dependency files, abstractions, Kubernetes/Terraform identities,
exclusive test resources, external mutations, and architecture decisions
serialize. Unknown ownership forces a singleton wave. External database,
Kubernetes, Terraform, migration execution, and publication domains also
reserve class-wide repository claims, so separate lanes cannot run those
live-action classes concurrently merely by using different keys.

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
Every task starts in a distinct worker session. The coordinator creates only
the active capacity batch; later batches open after the current batch commits.
Each assignment references an immutable private incoming-handoff record with
the accepted commits, paths, summaries, decisions, risks, validation, and
review evidence from all earlier completed waves and batches. Only the first
batch of the first wave has an empty predecessor list.

Immutable worker-assignment v7 records also carry canonical guardrails and the
exact helper/workspace-manifest paths for the first transition. Unless
the exact assignment explicitly authorizes an action, workers stay inside the
assigned worktree/private state; installed skill instructions/helpers and
standard local executables are read/execute-only when required. Workers do not
modify installed files, intentionally write other paths, or access network,
credentials, external services, or live runtimes. Applicable prompt and
repository constraints, implementation steps, and end-to-end validation are
repeated in the assignment so it and the incoming handoff are the worker's
complete task context.
Workers record a private heartbeat at least every 30 seconds. The coordinator
checks liveness on the same cadence. Dependency-free `standard` tasks warn at
240 seconds and stop at 300 seconds without a claimed-path edit or blocker.
Dependent `integration` tasks warn at 360 seconds and stop at 420 seconds.
A heartbeat becomes hard-stale at 240 seconds; that stop gate and the immutable
total budget remain independent of the recommended 30-second cadence.
Workers receive assignment-only fresh context with no inherited coordinator
transcript. Queued assignments do not consume a start budget; the coordinator
arms one only when a real worker slot is available. That worker reads its
assignment and makes `task-start` the first private transition after immediate
Git/cwd verification. It passes the embedded digest unchanged through the
embedded helper/workspace paths; `task-start` performs authoritative canonical
digest validation, so workers never guess JSON serialization. Incoming-handoff
reading and deeper preflight follow. The start budget is 60 seconds.
At the profile warning, `task-watch` tells the coordinator to demand an
immediate edit or blocker. Heartbeats are direct bounded calls;
background or autonomous heartbeat loops are forbidden. Workers do not reread the full
managed prompt or coordinator-only state after validating their assignment.
`task-start` is single-use, and mutations outside immutable write claims stop as
`WORKER_SCOPE_VIOLATION` instead of extending any liveness budget.

Workers never edit the shared handoff, managed specs, common docs, other refs
or worktrees, or the source checkout. An undeclared path requirement stops
with `REPLAN_REQUIRED` before edit or commit.

## Integration And Promotion

The coordinator verifies worker Git evidence independently, then merges task
branches into a temporary integration branch in stable task-ID order with
`git merge --no-ff --no-edit`. Shared managed specs, README/design docs, and
changelog remain coordinator-owned.

At planning, the coordinator verifies the exact Worktree-owned persistent lane
and acquires a generation lease. It never creates another promotion branch,
fetches, resolves a remote default, or mutates the source checkout.

After combined validation, integration `code-review`, and steering
and project-agent-instructions reconciliation, the unchanged clean lane branch
advances under a shared promotion lock with
`git merge --ff-only <verified-integration-SHA>` after the integration branch
is verified at that SHA. Before that promotion, clean verified worker
worktrees are removed and worker refs are deleted with exact expected-old
SHAs. This fast-forward promotion is the only point where tasks become done.

The integration worktree is removed after promotion, followed by exact-SHA
integration-ref deletion. Failures preserve exact resources for recovery. The
workflow never runs broad prune/gc, cherry-picks, rebases, squashes, pushes, or
force-removes.

After the last wave cleanup, final changed-surface `$align` evidence is sealed
before the lane generation is released. An interruption after the handoff is
marked done remains recoverable: repeating `run` finishes the same private
release instead of starting a new generation.

`integrate [project-folder]` requires both the recorded source checkout and
lane to be clean and free of Git operations, with no active generation. It
serializes by source ref, builds one exact two-parent candidate from the current
source head and latest lane head, runs combined validation and review on that
candidate, and promotes only the validated SHA through an expected-old
compare-and-set. Conflicts and uncertain state are retained. Success consumes
all pending generations, releases their claims, and rearms the same lane at the
merge head.

## Private State And Recovery

Coordinator v6, wave v4, mutable task-plane v5, immutable assignment v7/result v3,
incoming-handoff, and journal records live with the run
under the private prompt workspace. Every Git mutation is journaled before
execution and re-observed afterward. A repeated `run` resumes durable v5 truth
without recreating branches, worktrees, assignments, commits, or merges.

`orchestration/interop.json` uses schema v4 and binds one run to its exact lane,
incarnation, generation, and Worktree-owned lease. Worktree schema-v4 remains
the general linked-worktree ownership format; separate lane schema-v1 state and
immutable generation receipts add the persistent Task lifecycle without
changing that public Worktree schema.

Every execution-plane-v1 or coordinator-v1/v2/v3/v4/v5 run returns
`WORKFLOW_UPGRADE_REQUIRED`, including completed records. There is no legacy
read path, compatibility execution path, or migration command.

Prompt filenames stay stable. Submission order comes from private
`last_invoked_at`, not filenames or mtimes. Output never prints prompt bodies,
secrets, or internal IDs.

## Files

- `SKILL.md`: explicit four-action coordinator contract.
- `agents/openai.yaml`: UI metadata and explicit-only policy.
- `references/prompt-workspace.md`: private storage, routing, v2 state, errors,
  and sandbox behavior.
- `references/implementation-loop.md`: task analysis, wave lifecycle, worker,
  integration, promotion, cleanup, and recovery rules.
- `assets/handoff-template.md`: coordinator-owned queue and wave evidence.
- `scripts/prompt_workspace_execution.py`: parsing and deterministic scheduler.
- `scripts/prompt_workspace_waves.py`: journaled worktree lifecycle.
- `scripts/prompt_workspace_lanes.py`: private Worktree-owned lane adapter.
- `scripts/prompt_workspace_interop.py`: generation lease bridge.
- `scripts/prompt_workspace_intake.py`: prompt routing and steering.
- `scripts/prompt_workspace_specs.py`: managed specification validation.
- `project-agent-instructions`: shared conditional selected-project
  `AGENTS.md` owner and deterministic provenance helper.
- `scripts/test-task-execution.py`: scheduler and v1-boundary tests.
- `scripts/test-task-waves.py`: disposable real-Git lifecycle tests.
- `scripts/test-worktree-interoperability.py`: composed lane-generation,
  promotion, cleanup, interruption, and source-isolation tests.
- `scripts/test-prompt-workspace.py`, `test-task-specs.py`, and
  `test-task-implementer-contract.py`: storage, spec, and contract tests.

## Boundaries

- Explicit invocation only; generic parallel requests do not trigger it.
- Public surface remains exactly `workspace init`, `run`, `integrate`, and
  `workspace remove`.
- External database, Kubernetes, Terraform, migration, and publication actions
  remain singleton and need separate explicit authority.
- Use `$align` after the final promoted wave; use `$sdlc-start run <prompt>` for Agentic
  SDLC.
