# Implement

Use `$implement` for the current unambiguous plan, `$implement <request>` for a
new direct objective, or `$implement continue [project-folder]` across sessions.
Explicit-only; no additional public flags except `-h, --help`.

One run owns one integration checkout and fresh task worktrees. Shared Worktree
primitives freeze execution identity, schedule ready tasks, validate quiescent
results, integrate full waves and deliver locally. The frozen basis includes
mandatory workflow references, supporting owner instructions, and the worker
result schema; changed inputs require replanning before dispatch.
Initial specifications are published in the run integration checkout before
execution is frozen. The shared owner journals and commits that exact pair;
the accepted source and unrelated dirt stay unchanged. Preparation rejects
uncommitted source specs, unrelated integration changes and missing publication
evidence before workers start.
Before locking tasks, the coordinator copies each slice's concrete interfaces,
schema, fixed values, constraints and acceptance checks into the task fields
carried by its assignment. Missing task context blocks dispatch.
The native host must permit the required Git metadata and private-state writes.
Workspace file access alone is insufficient. Unavailable permission is an
`ENVIRONMENT_BLOCKER` before spec edits; Implement never bypasses host controls.
See [the skill](SKILL.md),
[objective contract](references/objective-contract.md) and
[execution loop](references/implementation-loop.md).
