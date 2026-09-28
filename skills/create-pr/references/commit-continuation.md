# Commit continuation for one PR task

An explicit unmanaged PR task includes the validated commits and pushes needed
to resolve branch-owned checks. A call from `create-pr` to the shared `commit`
skill does not require another standalone user invocation. The calling root
agent must still determine that the actual task authorizes those effects;
mentioning a skill, quoting a command or editing its source is not Git authority.

## Private transaction protocol

1. Run the installed Worktree publication guard, select the feature branch and
   resolve the default/base. Active SDLC uses its publication-only route.
2. Complete local checks and review the whole repository diff. Use the canonical
   installed `commit/scripts/commit_transaction.py prepare` with the native
   session ID, canonical authorization/claim paths, `--requested-action create-pr`,
   the hook's original `--intent-sha256`, and `--pr-base <base>`.
3. Review the returned candidate tree. Call the canonical helper's `execute`
   with the returned token, exact reviewed tree and concise message. The helper
   alone stages with repository-root `git add -A`, checks the staged diff,
   commits with normal hooks and proves its exact direct-child result.
4. Merge the refreshed base when needed, validate the result, push with the
   explicit `origin HEAD:<branch>` refspec, and inspect the current-head checks.
5. For another safe branch-owned repair, repeat prepare/review/execute using
   the same original receipt digest and base. Each commit gets a new one-shot
   claim. Do not create a receipt, edit private state, reuse an old token for a
   new candidate, or switch to raw Git because a check fails.
6. When checks finish and publication is verified, call the helper's private
   `review` with the last token, committed SHA and tree plus `--complete-pr`.
   Completion requires a clean authorized head and permanently closes this
   grant. Close it as well when abandoning a task after a completed commit.
   A blocked unresolved claim remains inert evidence, not permission to act.

These flags are private implementation details, not public skill options.
Keep tokens transient. A task with no local commit needs no commit grant.
Persist only bounded identities/digests under the selected agent's private
transaction root. Never hand-write authorization, grant or claim files.

## Scope and recovery

The immutable grant binds the original root receipt, native session, exact
worktree/common directory, selected feature ref, effective origin fetch/push
destinations and base lineage. Effective URLs are hashed, never stored. The
helper revalidates that scope at prepare, execute and review. Ordinary commit
and commit-push stay single-use; Worktree and Task Implementer retain their
existing delegated routes. Active SDLC still denies ordinary helper commits.

Successive commits must follow this grant's exact completed predecessor. Only
two-parent merges with the owned history as first parent and the recorded
forward-moving base as second parent may intervene. Rerun affected checks after
a merge; ancestry proof does not review conflict-resolution content. A changed
remote, base, branch, native session or unrelated intervening commit blocks
continuation instead of silently expanding authority.

A normal hook failure with no commit may retry after its cause is corrected if
HEAD remains at that attempt's base. Interrupted exact-child commits use the
existing recovery path. Hook-modified or otherwise uncertain commits remain
`REVIEW_REQUIRED` until their actual clean direct-child commit and tree are
reviewed through `review`. Do not reset, amend, unstage or discard evidence to
continue. A closed receipt cannot reopen a task; a fresh authorized task may
create a new grant.

## Validation evidence

`commit/scripts/test-commit-transaction.py` exercises successive commits, safe
retries, closure, drift, base merges and recovery in disposable repositories.
This deterministic evidence is separate from native host trigger evaluation.
The durable learning is to carry parent-task authority through the owning
transaction, while reviewing and consuming each individual commit separately.

Git documents effective URL expansion and separate push destinations in
[git remote](https://git-scm.com/docs/git-remote). GitHub's
[PR checks command](https://cli.github.com/manual/gh_pr_checks) reports check
state; passing local tests does not establish passing current-head CI.
