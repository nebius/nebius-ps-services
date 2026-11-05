# Implement

Use `$implement` for the current unambiguous plan, `$implement <request>` for a
new direct objective, or `$implement continue [project-folder]` across sessions.
Explicit-only; no additional public flags except `-h, --help`.

One run owns one integration checkout and fresh task worktrees. Shared Worktree
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
spawning. Missing host capability blocks execution.
An early exit before `task-start` remains `WORKER_FAILED`; a validated bounded
`prestart_result` supplies diagnosis only, never result or recovery authority.
Both workflows allow five minutes from arming to `task-start`, followed by the
one-hour work limit. Prepare handoff details before arming and spawn immediately;
persist launch observations afterward. Repeated arming never renews the window.
Both coordinators publish canonical specs through `maintain-project-specs`.
Initial specifications are published in the run integration checkout before
execution is frozen. The shared owner journals and commits that exact pair;
the accepted source and unrelated dirt stay unchanged. Preparation rejects
uncommitted source specs, unrelated integration changes and missing publication
evidence before workers start.
Before locking tasks, the coordinator copies each slice's concrete interfaces,
schema, fixed values, constraints and acceptance checks into the task fields
carried by its assignment. Missing task context blocks dispatch.
Multiline task fields retain schema examples and acceptance checks in the
serialized assignment. Empty or duplicate fields and malformed task headings
are rejected before preparation instead of silently dropping task context.
The native host must permit the required Git metadata and private-state writes.
Workspace file access alone is insufficient. Unavailable permission is an
`ENVIRONMENT_BLOCKER` before spec edits; Implement never bypasses host controls.
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

## Issue resolution across delivery

The [shared handoff contract](../worktree/references/issue-resolution.md) assigns
review and saved plans to review-github-issues, local implementation and individual
acceptance evidence to Implement, concise comments and verified closing references
to create-pr, and merge/issue-state verification to merge-pr. GitHub performs
native issue closure on default-branch merge when enabled. A partial fix or mere
issue mention never qualifies. Each delivery owner retains its explicit authority;
review does not start implementation, PR creation or merge.
