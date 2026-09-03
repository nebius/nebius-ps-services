# Commit Push

`commit-push` commits all current local changes across the whole Git
repository on the active non-default feature branch and pushes that branch to
`origin`. It is intentionally smaller than `create-pr`: it does not create
PRs, change branches, merge, rebase, or repair remote divergence.

## What It Does

- Rejects publication from every state-classified managed child, integration
  candidate, nested worker, or inconsistent ownership claim and routes it to
  its local owner; only the accumulated source branch may be pushed.
- Verifies the current Git state is safe for a branch-local commit and push.
- Reuses the claim-bound commit transaction, which previews and stages the
  complete repository diff with repo-root `git add -A`.
- Creates a commit with a user-provided or generated message after reviewing
  the temporary-index candidate.
- Uses exact remote queries, constrained tracking-ref refresh, and one
  non-force current-branch push to `origin`.
- Stops on active pre-push or reference-transaction hooks rather than
  bypassing hidden project effects.
- Reports whether the final worktree is clean.

## Architecture

```text
Current git branch
  |
  v
Safety checks
  |
  v
Claim-bound candidate preparation
  |
  v
Candidate review and validation
  |
  v
Commit when needed
  |
  v
Push current branch
  |
  v
Final status report
```

## Workflow

1. Detect the repository root, current branch, `origin`, default branch, and
   current worktree status.
2. Stop on unsafe states such as default branch, detached `HEAD`, missing
   `origin`, unknown default branch, unresolved conflicts, in-progress Git
   operations, missing or mismatched `origin/<branch>` upstreams, or remote
   divergence.
3. If the branch is clean and already pushed, report a no-op.
4. If the branch is clean but ahead, push the existing commits.
5. If the branch is clean, has no upstream, and has local work relative to the
   default branch, push it with upstream tracking.
6. If the branch is dirty, use the canonical commit transaction to preview the
   complete repository tree, validate it, and create the local commit with
   normal hooks before pushing.
7. Report the final branch status and whether the worktree is clean.

## Core Concepts

- The skill is current-branch only.
- `git add -A` is mandatory inside the claim-bound helper and always runs from
  the Git repository root because monorepo changes often span projects.
- The current working directory, service folder, chart folder, or package
  folder never narrows the commit scope for this skill.
- Divergence recovery is intentionally out of scope; it needs a separate
  explicit request.
- The remote branch refresh uses one full `refs/heads/<branch>` source ref and
  suppresses `FETCH_HEAD`, tags, automatic maintenance, and commit-graph
  writes.
- Raw staging and commit stay denied; `$commit-push` authorizes the existing
  local transaction as part of the same publication workflow.
- Commit hooks run normally. An active pre-push or reference-transaction hook
  stops the remote transition because its project effects are not bounded.
- Idempotence means safe no-op or push-only behavior, not hidden branch repair.

## Files

- `SKILL.md`: Runtime workflow, guardrails, commands, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
