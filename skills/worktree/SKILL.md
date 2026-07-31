---
name: worktree
description: "Requires explicit invocation to create, publish, open a PR for, or safely remove an isolated full-repository Git worktree for one monorepo project. Use `$worktree` or `$worktree add` to start project work from `origin/main`, `$worktree push` or `$worktree create-pr` for serialized publication, and `$worktree remove` only after proof and after any nested Task Implementer or Agentic SDLC lease is released. Do not use implicitly, for parallel-agent orchestration, or for ordinary commit/PR requests outside managed worktrees."
---

# Worktree

## Purpose

Manage one project-scoped workflow in an isolated linked Git worktree while
preserving other in-progress projects in the primary monorepo checkout. Every
linked worktree contains the full repository; the selected project directory
is the operating scope, not a partial checkout.

## Invocation Policy

Require explicit invocation. Keep `policy.allow_implicit_invocation: false` in
`agents/openai.yaml`.

## Public Interface

Expose exactly these actions:

```text
$worktree [add] [<task description>] [--project <repo-relative-directory>]
$worktree push [<commit-message request>]
$worktree create-pr [<title/body request>]
$worktree remove <generated-worktree-name>
```

- Treat no action as `add`.
- Infer the project from the exact current directory unless `--project` is
  provided.
- Derive a short public-safe task slug from the request; use `work` when no
  description exists. Never put prompt text, secrets, customer names, private
  endpoints, or confidential identifiers in a branch or directory name.
- Generate new names from the constant `project` prefix, the public-safe task
  slug, and random suffix. Never derive them from the repository name or
  project scope because those paths may contain confidential identifiers.
- Keep generated names internal until `add` returns them. Do not require the
  user to choose a unique identifier.
- Do not add compatibility aliases, a merge action, a force-remove action, or
  a standalone installed CLI.

## When To Use

- The user explicitly invokes `$worktree` to isolate one monorepo project from
  other active project work in the primary checkout.
- The user explicitly invokes `push`, `create-pr`, or `remove` for a worktree
  previously created by this skill.

## When Not To Use

- Do not use implicitly for ordinary feature work, commits, pushes, or PRs.
- Do not use for dependency-wave workers or multi-agent orchestration; use
  `task-implementer` when explicitly requested for that workflow.
- Do not execute the helper on Windows; its lifecycle lock uses Python's
  Unix-only `fcntl` module. Use a Unix-like host instead.
- Do not use `push` or `create-pr` from an unmanaged checkout.
- Do not use `remove` to abandon dirty, unpushed, open-PR, closed-unmerged, or
  unverifiable work.

## Inputs

- Python 3 on a Unix-like host and a current Git CLI.
- A Git repository with an `origin` remote and `origin/main`.
- Git credentials that can fetch and, for `push` or remote cleanup, update the
  exact managed branch.
- An authenticated GitHub CLI for `create-pr` and `remove`; cleanup queries all
  PR states before proving that a branch is merged or was never published.
- An optional repository-relative project directory for `add`.
- An optional public-safe task description, commit message, or PR title/body.
- For cleanup, the exact generated worktree name. Run the helper from the
  primary checkout even when the user invokes the skill from a linked
  worktree.

## Required Reads

- Read `references/lifecycle.md` before executing any action.
- For `push`, read and apply the current `commit-push` skill after acquiring
  the private managed-worktree publication reservation.
- For `create-pr`, read and apply the current `create-pr` skill with base
  `main` after acquiring the private managed-worktree publication reservation.
- Inspect applicable repository instructions and the current Git status before
  every mutation.
- Verify version-sensitive Git and GitHub CLI behavior against current official
  documentation before changing this skill's commands or safety rules.

## Writes

- `add` creates a sibling `<repo-name>-worktrees/` directory, one linked
  worktree, one `worktree/<generated-name>` local branch, and branch-associated
  metadata in local Git configuration. It also records durable private
  ownership state under `<repo-name>-worktrees/.worktree-skill/` after fetch
  preflight and before the first managed branch or worktree mutation.
- `push` may stage the full linked worktree, commit, and push the managed branch
  through `commit-push`.
- `create-pr` may validate, commit, update from `origin/main`, push, and create
  or reuse a GitHub PR through `create-pr`.
- `remove` may remove the clean linked worktree, delete its local branch, and
  conditionally delete its exact unchanged remote branch after merged-PR proof.
- Private task leases and publication reservations live beside ownership
  manifests under `<repo-name>-worktrees/.worktree-skill/`; they never enter
  tracked files or change the public action set.
- Never write workflow state into tracked project files.

## Process

### `add` (default)

1. Resolve the installed skill directory and run the helper from the primary
   checkout or selected project directory:

   ```text
   python3 <skill-dir>/scripts/worktree_manager.py add \
     [--project <repo-relative-directory>] \
     --task-slug <public-safe-slug>
   ```

2. Let the helper fetch `origin`, require `origin/main`, validate the selected
   scope, preserve unrelated primary-checkout dirt, and block dirty or
   branch-divergent work in the selected project.
3. Let the helper create and re-observe the full-repository linked worktree
   with `--no-track` under the sibling worktree parent.
4. Return the generated name, branch, base SHA, worktree root, and
   `<worktree>/<scope>` working directory. Report unrelated primary changes as
   preserved, not included.

### `push`

1. From the recorded project directory inside the linked worktree, acquire the
   private publication reservation:

   ```text
   python3 <skill-dir>/scripts/worktree_manager.py publication-begin \
     --publication-action push
   ```

2. Keep the returned reservation identity private. Stop if managed identity
   fails, a task run owns the outer branch, or dirty/committed paths escape the
   recorded project scope.
3. Apply `commit-push` from the returned `scope_cwd`. Preserve its repo-root
   `git add -A`, validation, divergence, explicit-refspec, and reporting rules.
4. Only after `commit-push` returns successfully, release the exact private
   reservation with `publication-end --publication-action push`. If execution
   is interrupted or uncertain, retain it and repeat `$worktree push`; the
   repeated action resumes the same reservation before reconciling publication.

### `create-pr`

1. Acquire `publication-begin --publication-action create-pr` from the recorded
   project directory and keep its reservation identity private.
2. Apply `create-pr` on the same managed branch with base `main`. Reuse an open
   PR and preserve its validation, current-base merge, explicit push, and check
   reporting rules.
3. Only after the child flow returns successfully, release the exact private
   reservation with `publication-end --publication-action create-pr`. On an
   interrupted or uncertain attempt, repeat `$worktree create-pr` to resume it.
4. Return the PR URL and readiness. Do not wait for user merge and do not clean
   up automatically; tell the user to invoke `$worktree remove` after merge.

### `remove`

1. Resolve the generated name from the managed worktree when needed, then run
   the helper from the primary checkout with that exact name:

   ```text
   python3 <skill-dir>/scripts/worktree_manager.py remove \
     --name <generated-worktree-name>
   ```

2. Let the helper re-observe Git and GitHub truth, require clean status, and
   require either an exact merged PR/head match or a never-published local
   branch with no commits beyond its recorded base.
   It holds the shared lifecycle lock throughout cleanup and blocks while a
   task lease or publication reservation remains.
3. Remove the worktree without force before deleting the local branch. Delete
   the local ref atomically with its verified head as the expected old value;
   do not use ancestry-only or unconditional branch deletion.
4. Delete a remaining remote branch only with an exact expected-SHA lease.
   Report any retained local or remote resource and the exact rerun command.

## Idempotency

- Re-observe worktrees with `git worktree list --porcelain -z` and reconcile
  them with the durable ownership manifest and branch metadata; do not trust a
  constructed path, namespace, PR, or any one metadata source alone.
- `push` inherits `commit-push` no-op behavior and `create-pr` reuses an open
  PR for the same branch.
- Repeating the same interrupted publication action resumes its exact private
  reservation. A different publication action, task lease, or cleanup remains
  blocked until that reservation is reconciled; reservations never expire or
  auto-clear.
- Repeated `remove --name <exact-name>` from the primary checkout resumes from
  the remaining worktree, local branch, or remote branch. A fully removed
  exact name returns `already-removed`. Once cleanup begins, its recorded
  expected head is immutable; any surviving ref that advances is retained.
- Never recreate, reset, adopt, or delete a colliding foreign path or ref.

## Failure Handling

- On add preflight failure, make no worktree or branch mutation. A durable
  planned manifest is written before the first Git mutation and is either
  promoted to active, removed after complete rollback, or retained as recovery
  evidence.
- If post-create metadata or identity verification fails, roll back only the
  provably clean branch still at the recorded base; otherwise retain and
  report it. A retained `planned` or `recovery` manifest authorizes only
  bounded cleanup of the exact registered path and branch while they remain
  clean at the recorded creation base, even when branch metadata is partial.
- Dirty scope, scope escape, branch/path mismatch, remote-head drift, missing
  PR proof, and cleanup races fail closed with resources retained.
- Leases and reservations serialize cooperating skill workflows; they are not
  an OS sandbox against arbitrary processes. Stop and re-observe the checkout
  if an uncoordinated writer may be changing it.
- An active v2 `task-implementer` or `agentic-sdlc` owner lease blocks
  `inspect`, `push`, `create-pr`, and `remove` until every internal resource is
  cleaned, the outer branch is clean at the final promoted head, and the
  owner-specific final gates release the lease.
  Missing or malformed coordination state fails closed; there is no TTL,
  process-ID recovery, force-clear, or migration fallback.
- A failed exact-lease remote deletion may leave only the remote branch; rerun
  `remove` from the primary checkout with the generated name.

## Must Not

- Never use `git worktree remove --force`, `rm -rf`, broad
  `git worktree prune`, `git gc`, reset, rebase, cherry-pick, or force-push.
- Never use `git branch -D` or delete a local branch without an atomic expected
  old SHA.
- Never delete an open or closed-unmerged PR branch.
- Never delete a remote branch whose current SHA differs from the merged PR
  head proved during cleanup.
- Never narrow staging for `push` or `create-pr`; stop when repo-root
  `git add -A` would include paths outside the recorded project scope.
- Never claim a PR is merged, a worktree is clean, or cleanup completed without
  current command evidence.

## Completion Criteria

- `add`: the registered worktree is clean, on the generated branch, at the
  fetched `origin/main` SHA, and the project scope exists inside it.
- `push`: `commit-push` reports the exact managed branch pushed and final
  linked-worktree status.
- `create-pr`: `create-pr` returns the PR number/URL and terminal or explicitly
  pending readiness state; cleanup has not run.
- `remove`: every safely removable managed resource is gone, or every retained
  resource and blocker is reported without destructive fallback.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return the action, generated name, branch, worktree and scope paths, base or
head SHA, validation performed, pushed branch or PR URL when applicable,
cleanup result, preserved unrelated primary changes, and any retained-resource
blocker. Distinguish local static evidence from remote GitHub evidence.
