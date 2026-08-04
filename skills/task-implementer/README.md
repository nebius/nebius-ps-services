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

Before resources exist, replanning replaces the resource-free planned tail.
The coordinator index keeps completed waves plus the replacement schedule;
superseded planned wave files remain blocked history outside final completion.
After a final promoted wave is cleaned, replanning can append an isolated
correction tail found by integration review before finalization.

When initialized inside a linked worktree created by the `worktree` skill, the
entire task run is nested under that outer branch. The exact current outer
`HEAD` becomes the worker base, all wave promotions return to that branch, and
a private v4 lease with owner kind `task-implementer` blocks outer integration
and removal through final alignment. The task coordinator never fetches or
bases workers on the remote default in this case. After release, the managed
child returns an exact `$worktree integrate <generated-name>` handoff and the
coordinator stops for a fresh explicit user invocation; it is never pushed or
used as a PR head.

Resume, promotion, and release reconcile local `interop.json` against the exact
durable lease token and live clean outer Git head. Release persists a terminal
receipt; only an exact receipt can repair an interrupted local state write, and
any missing, contradictory, or different-SHA state fails closed. Each wave
promotion advances the lease's ordered history with an expected-head
compare-and-set, so a later wave cannot overwrite or skip durable promotion
proof.

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
or worktrees, or the primary checkout. An undeclared path requirement stops
with `REPLAN_REQUIRED` before edit or commit.

## Integration And Promotion

The coordinator verifies worker Git evidence independently, then merges task
branches into a temporary integration branch in stable task-ID order with
`git merge --no-ff --no-edit`. Shared managed specs, README/design docs, and
changelog remain coordinator-owned.

At planning, the coordinator resolves the actual `origin` default. If the
clean project checkout is on that branch, it automatically creates and switches
to a deterministic `feature/task-<run-hash>` promotion branch; otherwise it
reuses the existing non-default branch.

After combined validation, integration `code-review`, and steering
and project-agent-instructions reconciliation, the unchanged clean primary
branch advances under a shared promotion lock with
`git merge --ff-only <verified-integration-SHA>` after the integration branch
is verified at that SHA. Before that promotion, clean verified worker
worktrees are removed and worker refs are deleted with exact expected-old
SHAs. This fast-forward promotion is the only point where tasks become done.

The integration worktree is removed after promotion, followed by exact-SHA
integration-ref deletion. Failures preserve exact resources for recovery. The
workflow never runs broad prune/gc, cherry-picks, rebases, squashes, pushes, or
force-removes.

After the last wave cleanup, final changed-surface `$align` evidence is sealed
before a managed outer lease is released. The result is an exact local
`$worktree integrate <generated-name>` handoff, followed by a stop for a fresh
explicit user invocation instead of an internal call to that public lifecycle.
An interruption after the handoff is marked done remains recoverable: repeating
`run` finishes the same private release instead of starting a new task run.

## Private State And Recovery

Coordinator v6, wave v4, mutable task-plane v5, immutable assignment v7/result v3,
incoming-handoff, and journal records live with the run
under the private prompt workspace. Every Git mutation is journaled before
execution and re-observed afterward. A repeated `run` resumes durable v5 truth
without recreating branches, worktrees, assignments, commits, or merges.

`orchestration/interop.json` uses schema v3 and binds a nested run to the exact
managed outer identity and its worktree-owned lease. Completed prompt history is archive-only
after the outer worktree has itself been removed; it is never migrated to a
different workspace identity.

Every execution-plane-v1 or coordinator-v1/v2/v3/v4/v5 run returns
`WORKFLOW_UPGRADE_REQUIRED`, including completed records. There is no legacy
read path, compatibility execution path, or migration command.

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
- `scripts/prompt_workspace_interop.py`: optional managed-outer lease bridge.
- `scripts/prompt_workspace_intake.py`: two-command routing and steering.
- `scripts/prompt_workspace_specs.py`: managed specification validation.
- `project-agent-instructions`: shared conditional selected-project
  `AGENTS.md` owner and deterministic provenance helper.
- `scripts/test-task-execution.py`: scheduler and v1-boundary tests.
- `scripts/test-task-waves.py`: disposable real-Git lifecycle tests.
- `scripts/test-worktree-interoperability.py`: composed outer-worktree lease,
  promotion, cleanup, interruption, and remote-isolation tests.
- `scripts/test-prompt-workspace.py`, `test-task-specs.py`, and
  `test-task-implementer-contract.py`: storage, spec, and contract tests.

## Boundaries

- Explicit invocation only; generic parallel requests do not trigger it.
- Public surface remains exactly `workspace init` and `run`.
- External database, Kubernetes, Terraform, migration, and publication actions
  remain singleton and need separate explicit authority.
- Use `$align` after the final promoted wave; use `$sdlc-start run <prompt>` for Agentic
  SDLC.
