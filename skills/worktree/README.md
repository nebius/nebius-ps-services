# Worktree

`worktree` creates and manages one full-repository linked Git worktree for a
selected project inside a monorepo. It is explicit-only because its actions can
create local branches, commit and push work, create GitHub PRs, and delete
proved-complete local or remote branches.

## Requirements

- Python 3 on a Unix-like host; the lifecycle lock uses the Unix-only `fcntl`
  module.
- Git with an `origin` remote that advertises a symbolic default branch, plus
  credentials for the
  requested fetch, push, or exact-lease remote deletion.
- An authenticated GitHub CLI for `create-pr` and `remove` so PR state and exact
  head evidence can be verified.

## Public Actions

```text
$worktree [add] [<task description>] [--project <repo-relative-directory>] [--reuse <exact-name>]
$worktree push [<commit-message request>]
$worktree create-pr [<title/body request>]
$worktree remove <generated-worktree-name>
```

## What `--project` Means

`--project` selects an existing directory inside the repository and records
its repository-relative path as the managed project scope. It is an operating
scope, not a destination path or branch-naming option.

For example:

```text
$worktree add "improve reusable skills" --project skills
$worktree add "update cxcli" --project services/nebius-cxcli
```

For `--project skills`, the skill:

- verifies the existing `<repository>/skills/` directory and records `skills`
  as the scope;
- creates a full-repository linked worktree, then returns
  `<generated-worktree>/skills/` as the working directory;
- rejects selected-scope dirt or branch divergence during `add`; and
- requires later managed commits and publication changes to stay inside
  `skills/`.

`--project` does **not** create the selected directory, create a partial
checkout, choose the worktree destination, choose the base branch, or supply
the generated branch name. The task description provides a public-safe slug;
the skill combines that slug with a random suffix to generate the worktree and
branch identities independently of the project path.

When `--project` is omitted, the exact current directory inside the primary
checkout becomes the scope. For example, running from
`<repository>/skills/` selects `skills`, while running from the repository root
selects `.` and therefore scopes the lifecycle to the whole repository.

`add` resolves and verifies the actual `origin` default branch, creates the
sibling `<repo-name>-worktrees/` parent when needed, and returns the selected
project directory inside the new full-repository worktree. Dirty or unmerged
work in that project blocks creation; unrelated monorepo project changes are
preserved.
Generated names use a constant `project` prefix plus the public-safe task slug
and random suffix; repository and project-scope names are never copied into the
branch or directory identity.
The helper records its planned manifest after fetch preflight and before it
creates the managed branch or linked worktree.
An active lifecycle is reused only with `--reuse <exact-name>` and matching
scope/task identity; existing dirty changes and remote-default HEAD drift are
reported and left untouched. Drift blocks later publication without making
unfinished local changes inaccessible.

`push` and `create-pr` acquire a private action-bound publication reservation,
validate managed identity, and reject changes outside the recorded project
scope before reusing the repository's existing `commit-push` and `create-pr`
contracts. `publication-begin` returns the exact recorded default branch, ref,
and HEAD for the child PR flow; it never requires a guessed base. The
reservation closes the check-to-mutation race only among
cooperating worktree, Task Implementer, and Agentic SDLC lifecycle owners; it
is not an OS sandbox against arbitrary writers. It is released only after the
child workflow returns successfully. Repeating an interrupted action resumes
it; `create-pr` never cleans up automatically.

`remove` runs from the primary checkout with an exact generated name. It
requires durable ownership state, a clean worktree, and either exact
merged-PR/head proof or a never-published branch with no new commits. It removes
the worktree before atomically deleting its local ref at the verified SHA, then
conditionally deletes an unchanged remote branch with an exact SHA lease. It
never force-removes a worktree or replaces the recorded cleanup head on retry.
Interrupted setup can recover from partial Git metadata only while the exact
registered resources remain clean at their recorded creation base.

When `task-implementer` or Agentic SDLC runs from a managed worktree, a private
v3 owner lease holds the outer branch through internal execution, cleanup, and
final alignment. The lease blocks outer push, PR creation, and removal. Worker
branches/worktrees remain internal. Agentic SDLC releases only after alignment,
UAT, and documentation gates, then uses the normal PR publication reservation.
Publication reservations remain schema v2 and are independent of lease v3.

## Files

- `SKILL.md`: runtime routing, permissions, guardrails, and output contract.
- `agents/openai.yaml`: explicit-only UI metadata.
- `references/lifecycle.md`: exact lifecycle and recovery guidance.
- `scripts/worktree_manager.py`: deterministic identity, add, inspect, and
  remove helper.
- `scripts/worktree_state.py`: private atomic ownership-manifest persistence.
- `scripts/worktree_interop.py`: fail-closed task leases, publication
  reservations, and shared lifecycle locking.
- `scripts/git_promotion.py`: shared remote-default, promotion-lock, ff-only,
  and exact-ref deletion primitives.
- `scripts/test-git-promotion.py`: offline real-Git promotion contract tests.
- `scripts/test-worktree-manager.py`: offline real-Git lifecycle tests.
- `evals/trigger-prompts.md`: explicit trigger and boundary examples.
