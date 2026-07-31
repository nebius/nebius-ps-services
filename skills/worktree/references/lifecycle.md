# Worktree Lifecycle Reference

Use this reference for exact action sequencing and failure interpretation. The
Python helper is the canonical implementation of identity, path, ref, and
cleanup checks; do not rewrite its command logic ad hoc.

## Contents

- Managed identity
- Add lifecycle
- Push and PR handoff
- Cleanup proof
- Recovery
- Official references

## Managed Identity

A managed worktree has all of these properties:

- it appears in `git worktree list --porcelain -z`;
- its branch is `worktree/<generated-name>`;
- its absolute path is under the sibling
  `<repo-parent>/<repo-name>-worktrees/` directory;
- its branch-local Git configuration records the canonical repo-relative
  project scope, absolute path, fetched base SHA, and generated name;
- a private durable ownership manifest under
  `<repo-name>-worktrees/.worktree-skill/` agrees with its name, branch,
  primary checkout, path, project scope, and base SHA;
- its common Git directory matches the primary checkout;
- its project working directory is `<worktree>/<recorded-scope>`.

Treat a disagreement between live Git state, the ownership manifest, and
branch metadata as a blocker. Never adopt a path or branch merely because its
name resembles this convention or because a similarly named PR exists.
For newly generated identities, use the constant `project` prefix plus the
public-safe task slug and random suffix. Do not derive a public branch or path
component from the repository name or recorded scope.

## Add Lifecycle

Resolve the primary checkout from the first non-bare worktree record. `add`
must start in that primary checkout, although the current directory may be a
nested project folder.

Before managed branch or worktree creation:

1. Require a named branch, `origin`, no in-progress Git operation, and no
   unresolved conflicts.
2. Fetch `origin`, require `origin/main`, and record its exact SHA.
3. Canonicalize the optional repository-relative project path and reject any
   escape outside the Git root.
4. Reject staged, unstaged, or untracked files in the selected project.
5. Compare the current branch with its `origin/main` merge base and reject
   branch-introduced changes in the selected project unless the selected-scope
   tree is exactly equal to `origin/main`. The exact-tree exception supports a
   squash-merged change whose old feature branch is no longer ancestry-merged.
   This deliberately allows an old or dirty feature branch when its changes
   are confined to another monorepo project.
6. Create the sibling parent idempotently, but reject a symlink parent.
7. Check the candidate filesystem path, registered worktrees, local refs, and
   remote refs before creating anything.

The fetch in step 2 updates remote-tracking state before the planned ownership
manifest exists. Write that manifest after preflight and before creating the
managed branch or linked worktree; do not describe it as preceding every Git
mutation.

Creation uses the fixed base and no upstream:

```bash
git worktree add \
  --no-track \
  -b "worktree/<generated-name>" \
  "<repo-parent>/<repo-name>-worktrees/<generated-name>" \
  origin/main
```

Write a private `planned` ownership manifest before creation. After creation,
write local Git metadata, re-observe the exact path, branch, base SHA,
cleanliness, common Git directory, and scope directory, then promote the
manifest to `active`. Roll back only a clean just-created worktree whose branch
still equals the recorded base; otherwise retain a `recovery` manifest.

## Push And PR Handoff

Acquire the action-bound private publication reservation before either handoff.
The reservation operation performs the same scope-clean inspection, fetches
current `origin/main`, validates managed identity, and rejects:

- dirty tracked or untracked paths outside the recorded scope;
- branch-owned committed paths outside the recorded scope;
- a path, branch, common-directory, or metadata mismatch.

After a passing inspection:

- `push` follows `commit-push` without changing its whole-repository staging or
  divergence rules;
- `create-pr` follows `create-pr` on the same branch with base `main` without
  changing its validation, merge-from-base, push, PR reuse, or check-waiting
  rules.

Keep the reservation identity private and release it only after the child
workflow returns successfully. If the process stops or the result is uncertain,
leave the reservation in place and repeat the same public action. The same
action resumes its reservation; a different publication action, task lease, or
cleanup is blocked. Reservations have no expiry, PID recovery, or force-clear
path.

This is cooperative lifecycle serialization, not an OS sandbox. It prevents
participating `worktree`, Task Implementer, and Agentic SDLC owners from racing,
but it cannot stop an arbitrary process from editing the checkout. If another
writer may be active, stop and re-observe the worktree before publication.

The scope check is what makes those existing repo-root `git add -A` contracts
safe for this project-isolation workflow.

## Nested Coordinator Ownership

When `task-implementer` or Agentic SDLC starts inside a managed linked worktree,
it acquires a v2 worktree-owned lease with owner kind `task-implementer` or
`agentic-sdlc`. The lease is bound to the exact outer name, branch, path, scope,
common Git directory, private workspace/run, task scope, and starting `HEAD`.
Integration and worker resources are declared before creation and tracked
through `planned`, `present`, and `absent` states. Owner-specific branch rules
accept only `codex/ti-*` or `codex/sdlc/*` resources respectively.

The lease makes the task coordinator internal to the outer workflow:

- every wave or feature starts from the current exact outer branch `HEAD`;
- worker branches merge into a temporary integration branch, then verified
  fast-forward promotion advances only the outer worktree branch;
- per-wave cleanup removes internal worktrees and branches without force;
- the lease remains across all local work and final changed-surface alignment;
- outer inspect, push, PR creation, and removal remain blocked until release.

Release requires a clean outer worktree at the recorded promoted head and no
registered or filesystem-visible internal worktree or branch. Missing,
malformed, mismatched, or incomplete state fails closed. There is no stale
lease auto-recovery, force-clear command, compatibility shim, or state
migration.

Agentic SDLC additionally requires final alignment, UAT, and documentation
evidence before releasing its run-level lease. It then acquires the normal
`create-pr` publication reservation. Lease v2 is independent from publication
reservation schema v1; upgrading leases does not invalidate reservations.

## Cleanup Proof

Always inspect before deleting:

```bash
git -C "<path>" status --short
git -C "<path>" log --oneline --decorate -5
```

Run cleanup from the primary checkout with an exact generated name. The helper
holds the shared lifecycle lock, fetches and prunes remote-tracking context,
requires matching durable ownership
state, then requires a clean worktree and no in-progress operation. Cleanup is
authorized only by one of:

1. exactly one PR for the exact head branch and base `main` is `MERGED`, and
   its `headRefOid` equals the local or remaining remote tip; or
2. the branch was never published, has no PR, and contains no commit beyond
   its recorded creation base.

If a remote branch exists, its current SHA must equal the proved head before
local cleanup starts. Remove in this order:

1. `git worktree remove <path>` without force;
2. atomically delete the local ref with
   `git update-ref -d refs/heads/<branch> <expected-head>` and remove its local
   branch configuration;
3. delete a still-matching remote ref with
   `--force-with-lease=refs/heads/<branch>:<expected-sha>` and an explicit
   delete refspec;
4. `git fetch origin --prune`.

The lease is a compare-and-delete guard, not permission to overwrite branch
history. If the remote advances, deletion fails and the remote branch remains.
The first authorized cleanup records an immutable expected head in the
`cleanup-pending` manifest. Every retry requires all remaining local and remote
refs to equal that head before it consults PR state or mutates another resource;
a newer merged PR never replaces the original cleanup proof.

## Recovery

- Worktree and branch remain: rerun from the primary checkout with the exact
  generated name.
- Interrupted setup with partial branch metadata: a `planned` or `recovery`
  manifest can remove only the exact registered worktree and branch when they
  are clean and still equal the recorded creation base. Any advancement or
  unregistered surviving path blocks recovery.
- Worktree is gone but local branch remains: rerun from the primary checkout;
  branch metadata supplies the remaining identity.
- Local branch is gone but remote remains: rerun from the primary checkout
  with the generated name; the durable manifest plus exact merged PR head
  supplies the ownership and deletion proof.
- All three resources are absent: return `already-removed`.
- Dirty, mismatched, advanced, unmerged, or ambiguous resources remain for
  operator review. Never broaden cleanup to nearby names.

## Official References

- [Git worktree](https://git-scm.com/docs/git-worktree): linked worktree
  lifecycle, clean-only removal, `--no-track`, and stable porcelain `-z` output.
- [Git update-ref](https://git-scm.com/docs/git-update-ref): deleting a ref only
  when its current value matches an expected old object ID.
- [Git push](https://git-scm.com/docs/git-push): exact-value
  `--force-with-lease=<ref>:<expect>` behavior.
- [GitHub CLI `gh pr list`](https://cli.github.com/manual/gh_pr_list): all-state,
  base/head filtering and PR JSON fields including `baseRefName`,
  `headRefName`, `headRefOid`, and `mergedAt`.
- [Python `fcntl`](https://docs.python.org/3/library/fcntl.html): Unix-only file
  locking used to serialize private lifecycle ownership.
