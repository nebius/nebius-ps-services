---
name: worktree
description: "Requires explicit invocation to create, locally integrate, exactly reuse, or safely remove a full-repository linked Git worktree for monorepo work. Use `$worktree add` from a clean named non-default source branch, `$worktree integrate` to merge committed child work back into that local source through a recoverable candidate, and `$worktree remove` after exact local proof. Do not use for push, PR creation, publication, or parallel-agent orchestration."
---

# Worktree

## Purpose

Create parallel full-repository child worktrees from the exact current local
feature branch, then serialize their proved commits back into that source
branch without publishing child branches.

## When To Use

- The user explicitly invokes `$worktree`, `$worktree add`,
  `$worktree integrate`, or `$worktree remove`.
- A monorepo developer wants separate linked checkouts for concurrent project
  work while retaining one local source branch for final publication.

## When Not To Use

- Do not invoke implicitly.
- Do not use from the default branch, detached HEAD, or a dirty primary
  checkout.
- Do not use child worktrees for push or PR publication. Publish only the
  accumulated source branch through the standalone Git/PR skills.
- Do not use as Task Implementer or Agentic SDLC's internal worker manager;
  those workflows may nest privately inside one managed child.

## Inputs

```text
$worktree [add] [<task description>] [--project <repo-relative-directory>] [--reuse <exact-name>]
$worktree integrate <generated-worktree-name> [--restart]
$worktree remove <generated-worktree-name>
```

- When a task description is present, derive one public-safe task slug from it.
  When it is absent, let the helper normalize the resolved project directory's
  basename; for example, scope `skills` defaults to task slug `skills`. An empty
  normalized basename falls back to `work`.
- Never pass raw prompt text, secrets, or a full project path into generated
  identities.
- `--project` selects an existing starting directory and descriptive label. It
  never creates a partial checkout or restricts changed paths.
- `--reuse` and lifecycle actions require the exact generated name.

## Required Reads

- Read [references/lifecycle.md](references/lifecycle.md) before integration,
  recovery, restart, or cleanup.
- For `integrate`, load `$align` for changed-surface validation. Load the
  relevant Task Implementer or Agentic SDLC handoff when that workflow produced
  the child head.

## Writes

- Creates a sibling `<repo-name>-worktrees/` parent, one generated child branch,
  one full linked worktree, branch-associated Git config, and private atomic
  ownership state under `.worktree-skill/` outside the repository checkout.
- Integration creates a private candidate branch/worktree, one exact merge
  commit, and advances the checked-out source branch only by verified
  fast-forward.
- Removal non-forcibly removes the child worktree, deletes its unchanged local
  ref with an expected-old SHA, deletes any exact released nested-lease
  receipt, and deletes its private manifest.
- Never commits user changes, fetches for lifecycle decisions, pushes, creates
  PRs, deletes remote branches, or writes private workflow state into the repo.

## Process

### Add

1. From the primary checkout, run the nonmutating preflight: verify the entire
   worktree is clean, no Git operation is active, the branch is named and not
   configured `origin/HEAD`, and the sibling parent/state path is canonical and
   non-symlinked. The helper repeats this preflight under its lifecycle lock
   before creating state or worktree resources.
2. Resolve the project directory. Derive and pass a task slug when the user
   supplied a task description; otherwise omit `--task-slug` so the helper
   derives it from the resolved directory basename. Then run:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py add \
     [--task-slug <public-safe-slug>] [--project <directory>] [--reuse <exact-name>]
   ```

3. Treat the returned source SHA as immutable creation evidence. Return the
   resolved task slug, generated name, branch, full worktree path, and selected
   starting directory.

### Integrate

1. Require child and primary source checkouts to be fully clean, the child work
   already committed, and every nested Task Implementer/Agentic SDLC lease
   released.
2. Start or resume the exact integration:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py integrate --name <exact-name>
   ```

3. If the helper reports `conflict`, preserve the returned recovery worktree.
   The developer resolves the conflict there and stages the resolution, then
   repeats the same command. Never merge or resolve in the primary or child.
4. If the helper reports `validation-required`, bind all checks to the returned
   candidate SHA and worktree. Run changed-surface `$align`, relevant tests,
   and any managed Agentic SDLC combined UAT non-mutatingly. If source changes
   are required, make and commit them in the child, then restart; do not repair
   the candidate outside conflict resolution.
5. Promote only the exact validated candidate:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py integrate \
     --name <exact-name> --validated-head <candidate-sha>
   ```

6. If the source moved, preserve the attempt. After review, explicitly use
   `--restart` to abort and remove only the exact owned candidate and rebuild
   from the current source head.

### Remove

1. Run from the clean primary checkout with the exact name:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py remove --name <exact-name>
   ```

2. Permit removal only when the child is unchanged from its base or its exact
   recorded merge remains reachable from the source branch. Keep any dirty,
   advanced, rewritten, leased, or unverifiable resource intact.

## Idempotency

- Exact `--reuse` preserves an active child and reports source-head drift; it
  never rebases or refreshes the child.
- Repeating a description-less add in the same project resolves the same
  project-basename slug and fails closed on the existing lifecycle unless the
  exact generated name is supplied with `--reuse`.
- Repeating `integrate` resumes the same durable `(source head, child head,
  candidate ref/path)` attempt. A promoted-but-unrecorded candidate reconciles
  from Git proof before cleanup.
- Repeating `remove` resumes from remaining exact local resources. Missing
  resources are accepted only when the durable manifest proves their identity.
- Nested lease release persists a schema-v4 terminal receipt. Replays require
  the exact owner, token, promoted head, clean outer checkout, and absent
  resources; the receipt is removed only with the outer lifecycle. Schema-v4
  ownership manifests retain the lease participation state and exact identity,
  while ordered promotion heads permit successive Task Implementer waves only
  through expected-head compare-and-set. Outer removal retains an exact private
  removal-intent snapshot until receipt, manifest, and final resource cleanup
  complete.

## Failure Handling

- Preflight failure creates no branch or worktree.
- Partial creation retains a recovery manifest only for exact clean resources
  at the recorded base.
- Merge conflict retains the private candidate and integration reservation;
  other integration into the same source is blocked until reconciliation.
- Source or child movement, malformed state, wrong merge parents, failed
  validation, failed fast-forward, or uncertain cleanup fails closed and
  preserves evidence.
- Older manifests, reservations, and lease schemas return
  `WORKFLOW_UPGRADE_REQUIRED`; there is no migration or compatibility path.

## Must Not

- Never use `git worktree remove --force`, broad prune/GC, reset, rebase,
  cherry-pick, force-push, or direct checked-out-ref updates.
- Never allow direct managed-child, integration-candidate, Task Implementer
  worker, or Agentic SDLC worker `commit-push`/`create-pr`; state-backed guards
  must route to the owning local promotion and integration workflow.
- Never treat `--project` as a staging or changed-path boundary. Git operations
  see the full linked checkout.
- Never auto-commit dirty child work or silently discard conflict resolutions.
- Never report integration or cleanup complete without re-observing exact Git
  identity, ancestry, parents, index, and cleanliness.

## Completion Criteria

- `add`: a clean child exists on a generated no-upstream branch at the exact
  captured local source SHA.
- `integrate`: the source checkout is clean at the exact validated two-parent
  merge SHA, durable proof is recorded, and the private candidate is absent.
- `remove`: the child worktree, local child ref, and manifest are absent; remote
  state is untouched.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return the action, resolved task slug, generated name, child/source branches
and exact SHAs, worktree/start-directory paths, candidate or recovery path when
applicable, validation status, retained resources, and precise next action.
