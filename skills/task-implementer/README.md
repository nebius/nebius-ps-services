# Task Implementer

`task-implementer` is an explicit-entry brownfield implementation coordinator.
It keeps prompts, revisions, steering, orchestration state, assignments,
results, and temporary worktree metadata outside the repository while product
changes remain ordinary reviewed commits.

## Public Workflow

```text
$task-implementer workspace init [project-folder]
$task-implementer workspace reuse [project-folder]
$task-implementer run <prompt-ref-or-file>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

Users never provide run, wave, task, branch, worktree, lifecycle, or private
state identifiers. Help is report-only and is not another workflow action.

- `workspace init` creates or verifies the private prompt workspace and
  persistent lane.
- `workspace reuse` reopens an existing verified workspace without repair or
  lane mutation.
- `run` compiles accepted intent, executes dependency waves, promotes verified
  results to the lane, and finalizes one released generation.
- `integrate` validates and consumes released generations into the recorded
  source branch.
- `workspace remove` removes only an idle, clean, fully integrated lane and
  preserves private history.

## Git Roles

| Location | Lifetime | Owner and purpose |
| --- | --- | --- |
| Primary checkout/source branch | Pre-existing | Operator-facing checkout; `run` leaves it unchanged and public `integrate` may advance it. |
| Persistent lane | Across runs | Task Implementer-owned promotion target for completed waves. |
| Wave integration worktree | One active wave | Coordinator-owned checkout for worker merges, combined checks, and promotion. |
| Worker worktree | One assignment | Worker-owned checkout constrained by immutable write claims. |
| Source integration candidate | One public integration | Coordinator-owned exact two-parent candidate used only when source and lane differ. |
| Private workspace | Persistent outside Git | Prompts, queues, receipts, assignments, evidence, recovery state, and summaries. |

All managed worktrees are full-repository linked worktrees. A selected monorepo
folder limits operating scope; it does not turn a worker into a partial Git
checkout.

![Task Implementer lifecycle from the primary checkout through dependency-wave
workers, verified lane promotion, run finalization, and public source
integration](../docs/images/task-implementer-lifecycle.png)

## Operator Flow

1. Run `workspace init` from the primary project checkout.
2. Edit a generated prompt and explicitly invoke `run`.
3. The coordinator binds accepted intent, resolves material clarification,
   plans tasks with dependencies and disjoint claims, and checkpoints only a
   completely reviewed resource-free lane candidate.
4. Each wave creates one integration worktree and one isolated worker worktree
   per active assignment. Workers implement, validate, review, and commit once.
5. The coordinator independently verifies worker evidence, merges commits in
   stable task order, runs combined validation and review, and fast-forwards
   the lane under exact Git checks.
6. Cleanup removes only verified resources and records anything retained.
7. After the final wave, changed-surface `$align` runs and finalization releases
   an immutable generation from the clean promoted lane head.
8. Explicit `integrate` validates the exact source/lane combination and advances
   only the recorded source branch through an expected-old compare-and-set.
   Push and PR workflows remain separate.

## Canonical Project Specs

Task Implementer uses `maintain-project-specs` as the single canonical parser,
paired publisher, and receipt owner while retaining its own execution state
machine.

- The root coordinator classifies each direct root-user statement and records
  durable requirements plus a covering ready design before plan lock.
- The exact root-intent digest and v2 spec receipt are bound into the plan and
  every worker assignment. They describe project truth but do not authorize a
  Task Implementer transition.
- Workers do not reclassify prompts or edit canonical specs. They return typed
  `spec_gaps`; non-empty gaps require coordinator reconciliation and replanning.
  Path inventories disable Git rename folding so a moved coordinator-owned
  source remains visible, and gap summaries/evidence pass the sensitive-text
  screen before private result publication.
- After combined implementation proof, the coordinator records separate
  implementation and verification evidence through the paired publisher.
- Hook status cannot deny a tool, request Stop continuation, interrupt a
  session, or gate cleanup, finalization, or integration.
- Task Implementer owns its prompt-impact claim/receipt schemas and plan-basis
  decisions. Canonical project-spec receipts are bound plan evidence, not a
  lifecycle authority.
- The hidden lifecycle authorization adapter and lifecycle contract-delta
  adoption command are retired.
- Project-agent validation is not a dispatch gate. Existing applicable
  `AGENTS.md` files are read as instructions; Task Implementer does not create,
  edit, retire, reload, apply, verify, or seal them automatically.
- Final cleanup and generation release require exact Task Implementer evidence,
  a clean lane, absent resources, and final `$align`—never a lifecycle seal.
  The workflow does not require terminal lifecycle seal evidence.
- Old private lifecycle artifacts may be inspected for diagnosis but are
  ignored for current workflow decisions and are not refreshed or migrated.

An explicitly invoked project-instruction workflow remains separate and keeps
its own mutation, provenance, conflict, and reload safety checks.
That implementation is `project-agent-instructions`, used only after an
explicit separate request.

## Prompt and Planning Contract

Only one meaningful `## Ask` is required. Other headings are optional. An
explicit run binds the accepted prompt revision and immutable snapshot. Editing
the same prompt is steering; running a different prompt while work is active
queues it FIFO.

The coordinator records stable clarification IDs and a complete workflow-owned
prompt-impact claim. Material ambiguity prevents plan lock. Material accepted
change requires a distinct plan identity. A bare `no_effect` label or unchanged
requirements bytes is not proof.

Each task records:

- stable ID and dependencies;
- exact or directory-prefix write claims;
- keyed conflict domains;
- validation commands and done criteria;
- immutable assignment and predecessor evidence;
- the inherited root-intent digest and exact project-spec receipt.

Overlapping work is combined or serialized. It is never dispatched in
parallel.

## Wave and Worker Safety

Wave states are:

```text
planned -> preparing -> running -> integrating -> promotion_pending -> promoted -> cleanup -> done|blocked
```

Task states are:

```text
planned -> assigned -> running -> committed -> merged|failed
```

The coordinator does not implement worker tasks. Each worker is bound to one
registered worktree, branch, assignment digest, session, task, and claim set.
The coordinator verifies commit ancestry, tree, changed paths, validation, and
result digests independently before integration. Every result carries a typed
`spec_gaps` list; workers propose gaps but never mutate canonical specs.

Internal branches are never pushed. Worker commits merge with
`git merge --no-ff --no-edit`; promotion uses verified fast-forward only.
Task Implementer never cherry-picks, rebases, squashes, broad-prunes, runs GC,
force-removes resources, or cleans ambiguous state.

## Cleanliness and Recovery

A clean managed worktree has no staged, modified, deleted, or untracked files
and no Git operation in progress. A resource-free new run may checkpoint one
fully reviewed related lane delta transactionally. Active or resumed state must
otherwise remain clean.

Every mutating transition records intent, canonical arguments, and observed
postconditions. Resume trusts coordinator-v7, task-plane, journal, Git,
Worktree, lease, and interop evidence—not PID, timestamps, Markdown, or
lifecycle state. Unknown writers, dirty resources, divergent heads, or
malformed evidence remain blocked and preserved.
Worker claim violations return `WORKER_SCOPE_VIOLATION`.

Coordinator v1 through v6 are unsupported. There is no compatibility reader or
automatic migration.

## Status and Output

The generated lane-status task is strictly read-only. It takes two matching
bounded observations, retries once after movement, and otherwise returns
`WORKSPACE_BUSY`. It never takes the mutation lock, refreshes a lane, computes
raw diffs, or exposes prompt content, internal IDs, branches, commits, private
paths, or secrets.

Successful completion returns the versioned public run summary and one next
public action. A repeated unchanged completed prompt returns the same summary.
If the primary source checkout is dirty at integration time, the next action is
to review and commit that complete checkout separately, then retry explicit
`integrate`.

## Validation

Focused implementation checks live in `scripts/test-task-*.py`. Cross-skill
source/installed parity and disposable integration verification live in
`task-implementer-test`.

Key implementation modules:

- `scripts/prompt_workspace.py`: private command dispatcher.
- `scripts/prompt_workspace_specs.py`: workflow-owned spec and prompt-impact
  state.
- `scripts/prompt_workspace_waves.py`: journaled worktree wave execution.
- `scripts/prompt_workspace_resume.py`: authoritative recovery planning.
- `scripts/prompt_workspace_lanes.py`: persistent lane and source integration.
