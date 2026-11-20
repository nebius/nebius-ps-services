# Implement

Shared Worktree reporting avoids duplicate aggregation observations and repeated
source reads within a coherent observation. Current execution authority, Git
identity and evidence checks remain in place across transitions and effects.
See [the shared owner](../worktree/README.md) for bounded-work checks and the
local performance harness; source results do not qualify installed/native runs.

Use `$implement` for the current unambiguous plan, `$implement <request>` for a
new direct objective, or `$implement continue [project-folder]` across sessions.
Explicit continuation always uses the shared intake continuation owner, even
after delivery, to bind the fresh session and return `ALREADY_COMPLETE`.
Reading completed status alone does not perform that transition.
Use `$implement status [project-folder]` to see owned work and the next action.
Explicit-only; no additional public flags except `-h, --help`.

Each objective automatically owns an outer checkout from a named non-default
branch, one active integration checkout and fresh task worktrees. The selected
project and shared inputs must be clean; unrelated sibling dirt is allowed after
the coordinator declares complete committed dependencies. Final promotion still
waits for the entire original checkout to be clean. Shared Worktree
primitives freeze execution identity, schedule ready tasks, validate quiescent
results, integrate full waves and deliver locally. The frozen basis includes
mandatory workflow references, supporting owner instructions, and the worker
result schema; changed inputs require replanning before dispatch.
Implement and SDLC use the same assignment-derived launch envelope and strict
per-attempt result receipts. Native adapters configure fresh assignment-only
context and observe termination; the common core owns result admission and
stops new work when a worker reports blocked work or a specification gap.
Native arm/recovery admission validates the selected home, project/run and
coordinator before declaring required project-state and runtime-lease write
directories. Adapters preserve sandbox and approval controls; directory grants
do not authorize unrelated state edits. The sequential adapter admits before
spawning. Verify capabilities before arming: supported explicit command cwd and
existing inherited directory grants can satisfy the envelope without spawn
setters. Worker prompts name the startup/heartbeat cwd separately from frozen
validation cwd. Missing host capability blocks execution.
An early exit before `task-start` remains `WORKER_FAILED`; a validated bounded
`prestart_result` supplies diagnosis only, never result or recovery authority.
Both workflows observe delayed startup after five minutes and review progress
periodically without expiring healthy workers. Prepare handoffs before arming
and spawn immediately. See [execution health](../worktree/references/execution-health.md).
Both coordinators publish canonical specs through `maintain-project-specs`.
Initial specifications are published in the run integration checkout before
execution is frozen. The shared owner journals and commits that exact pair;
the accepted source and unrelated dirt stay unchanged. Preparation rejects
uncommitted source specs, unrelated integration changes and missing publication
evidence before workers start.
Before locking tasks, the coordinator copies each slice's concrete interfaces,
schema, fixed values, constraints and acceptance checks into the task fields
carried by its assignment. Missing task context blocks dispatch.
The shared launch prompt also includes the task's exact executable validation
checks and Git-root working directory from its immutable execution basis.
Workers do not guess validation sidecar paths. This context preserves existing
failure handling and the coordinator's independent acceptance responsibility.
Multiline task fields retain schema examples and acceptance checks in the
serialized assignment. Empty or duplicate fields and malformed task headings
are rejected before preparation instead of silently dropping task context.
The native host must permit the required Git metadata and private-state writes.
Workspace file access alone is insufficient. Unavailable permission is an
`ENVIRONMENT_BLOCKER` before spec edits; Implement never bypasses host controls.
Requested worker, wave-task and retained-worktree limits are configured before
the first integration allocation. Otherwise allocation freezes the defaults
4/8/16; the coordinator cannot change them afterward. Native capacity remains
an independent limit, and repeated configuration must match the frozen policy.
See [the skill](SKILL.md),
[objective contract](references/objective-contract.md) and
[execution loop](references/implementation-loop.md).

Both coordinators use the shared stop-before-repair worker boundary. An
unexpected failed check or blocking review finding returns preserved evidence
and a blocked result before any repair or rerun. Only negative outcomes declared
in immutable assignment validation before execution are expected feedback.
Required safe teardown and lease release remain allowed. The owning coordinator
classifies and authorizes recovery or replanning; workers cannot relabel failures
or erase them with a later successful check.

Before actions with known access prerequisites, both worker envelopes direct
agents to supported tools and native approval mechanisms. An approval request
does not grant access, a denial remains a policy/authority stop, and an
unexpected executed failure still requires the blocked handoff. Recovery uses
the existing shared engine with a stopped predecessor, released runtime,
unchanged assignment and fresh identity; it never grants permission itself.

## Two projects from one aggregate branch

Suppose `feature/release-batch` is checked out in a monorepo containing
`project-a/` and `project-b/`. Start from committed history with clean selected
projects and shared inputs; unrelated sibling edits may remain. In one native
session opened in `project-a`, enter:

```text
$implement Add paginated search to this project's API and test it.
```

In a separate native session opened in `project-b`, enter:

```text
$implement Add CSV export to this project's CLI and test it.
```

Both sessions use the same selected agent home. Each creates its own automatic
outer; parallel task workers use further separate checkouts. Validated waves
merge into the project's integration checkout, then its outer, then the latest
`feature/release-batch`. The workflow records names and removes completed owned
resources. You do not invoke Worktree or remember generated paths.

From the original repository folder, inspect or resume by familiar folder:

```text
$implement status
$implement continue project-a
$implement continue project-b
```

Use a separate session for each active continuation. Status shows all projects
and names the owning workflow if one uses SDLC. `$sdlc-start <objective>` and
`$sdlc-start status [project-folder]` use the same admission and capacity registry.
Sibling scopes can run together; overlapping project or shared-file claims wait.
Default home-wide limits are four retained objectives, four active task workers
and sixteen task worktrees, further constrained by available native capacity.

You can keep committing on the aggregate while projects run. Dirty or switched
source checkouts make delivery wait; source history rewrites require recovery.
Commit/push/PR remain separately authorized actions. Publication serializes with
aggregation, and the aggregate branch stays until its children finish. Pushing
it does not publish unfinished child commits.

Open the [portable HTML presentation](../docs/parallel-project-workflow.html)
for the full flow, including waves, integration, conflicts, recovery and cleanup.
The [automatic project contract](../worktree/references/automatic-projects.md)
describes the exact lifecycle.

## Issue resolution across delivery

The [shared handoff contract](../worktree/references/issue-resolution.md) assigns
review and saved plans to github-issues, local implementation and individual
acceptance evidence to Implement, concise comments and verified closing references
to create-pr, and merge/issue-state verification to merge-pr. GitHub performs
native issue closure on default-branch merge when enabled. A partial fix or mere
issue mention never qualifies. Each delivery owner retains its explicit authority;
review does not start implementation, PR creation or merge.

## Factual reporting

Before specification bootstrap, Implement records known architecture even without a
plan. During execution it reports tasks, attempts, waves and observed timing; after
delivery it retains a complete or explicitly partial account. Private immutable
initial/terminal Markdown and JSON reports, current reports and deduplicated chat
summaries share the [statistics contract](../worktree/references/statistics.md).
Status includes this projection, actual running workers and reserved slots.
Completeness refers to the accepted objective. Missing timing remains unavailable;
there is no ETA and reporting cannot affect acceptance or scheduling.

## When delivery waits

The final response names the original branch, retained work and actual blocker.
After making the whole original checkout clean, resume from the original project:

```text
$implement continue "<original-project-folder>"
```

A promotion that already happened is reported as finalization or cleanup pending;
uncertain proof is reported as verification or recovery pending. Completed delivery
has no continuation command. The workflow never commits or stashes unrelated work.

UAT follows the [shared optional policy](../worktree/references/optional-uat.md).
Requested campaigns wait for a meaningful ready scope. An existing test environment
uses its project harness directly; container/Nebius campaigns require explicit
skill selection. Missing optional prerequisites leave a handoff without blocking
implementation or eligible publication. Actual failures and cleanup remain owned.
