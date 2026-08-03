# Local Worktree Lifecycle

## Topology

```text
clean local source branch
  -> child A / child B / child C develop concurrently
  -> one durable integration candidate at a time
  -> source branch advances by exact validated merge commits
  -> children are removed explicitly
  -> source branch is published once
```

Every linked worktree contains the full repository. The selected project is
only the initial working directory and label; related projects and shared files
may change in the same child.

## Creation Invariants

- Invoke from the primary checkout on a named non-default branch.
- Require staged, unstaged, untracked, and conflict state to be empty.
- Complete the full preflight without creating state, locks, or directories;
  then acquire the lifecycle lock and repeat the same preflight before mutation.
- Require the sibling worktree parent and private state root to be canonical
  directories and reject every encountered symlink or non-directory.
- Resolve configured `origin/HEAD` only to reject the default branch; do not
  fetch or compare source content with a remote.
- Resolve project scope before task identity. When no explicit task slug is
  supplied, normalize the resolved project directory basename and use `work`
  only when normalization is empty.
- Freeze the exact local source ref and SHA before writing planned ownership
  state and before creating the generated branch/worktree.
- Hold the private repository lifecycle lock from duplicate-manifest selection
  through planned state, Git worktree creation, and active-state verification.
- Re-observe the shared common Git directory, generated branch, exact HEAD,
  clean index/worktree, and selected starting directory.

## Integration Attempt

The durable reservation binds:

- managed child name, branch, worktree, and exact child SHA;
- source branch/ref and exact source-start SHA;
- private integration branch/path and token;
- state `planned -> present -> ready` and exact candidate SHA.

One source ref may have only one active reservation. A transient file lock
protects individual transitions; the durable reservation protects the
human-resolution interval.

Create the candidate from source-start, then merge the child using `--no-ff`.
The source checkout is never the conflict workspace. A resolved candidate must
be clean and have exactly two parents: first parent source-start, second parent
child-head.

Validation is external to the Python helper. The first helper call returns the
candidate path/SHA. `$align`, tests, and applicable Agentic SDLC UAT must not
change that candidate. The promotion call supplies the exact validated SHA;
the helper requires the candidate checkout and private ref to remain at that
SHA, rechecks parents, refs, cleanliness, and source/child stability, and
passes the immutable SHA—not the mutable private ref—to `git merge --ff-only`
inside the primary checkout.

## Recovery

- `planned` plus exact branch/worktree resources resumes preparation.
- `present` plus `MERGE_HEAD` resumes conflict resolution. The developer edits
  and stages only the resolution; the helper seals the merge on retry.
- `ready` returns the same candidate until its exact SHA is validated.
- If source moves, the attempt is stale. Preserve it until explicit
  `--restart`; restart aborts only the exact owned merge, requires the candidate
  clean after abort, removes it non-forcibly, deletes its ref by expected SHA,
  then starts from the new source.
- If source already equals a ready candidate after a crash, reconcile proof;
  never restart or layer another merge.
- After source promotion, write integrated manifest proof before candidate
  cleanup. A cleanup failure leaves the reservation so retry finishes safely.
- Downstream completion rechecks the recorded merge's exact parent order and
  requires the current local source ref to contain that merge; a stale manifest
  alone is never accepted as source-integration proof.

## Nested Coordinator Lease

Task Implementer and Agentic SDLC use lease schema v4. An `active` lease binds
the exact outer path/branch/common Git directory, owner, run, token, initial
head, ordered promotion heads, and every internal resource. Each promotion is
an expected-head compare-and-set, which permits successive Task Implementer
waves without accepting skipped or stale history. Ownership-manifest schema v4
independently records the lease participation state, owner, and token; a missing
lease file therefore cannot silently unlock integration or removal. Release
requires the clean outer checkout at the final promoted head and all resources
physically absent, then atomically changes the lease record and manifest marker
to `released`. A released receipt cannot be reacquired or updated; exact release
replay is safe, while missing, stale, or contradictory state fails closed. Outer
integration may proceed through a released receipt.
Before creating or resuming an outer integration reservation, rebind that
receipt to the manifest and live outer branch/path/scope/common-directory/head,
then rescan every recorded private path, symlink, registered worktree, and
branch. Removal performs the same identity and resurrection proof before
cleanup and receipt deletion.

## Removal Proof

An unused child is removable only at its captured base. An integrated child is
removable only when its unchanged head equals the recorded second parent and
the recorded merge remains reachable from the source ref. Source rewrites,
dirty state, active operations, active leases/reservations, or identity drift
retain all resources.

Remove the clean linked worktree without force, then delete the local child ref
with its exact expected old SHA. Never inspect, delete, or mutate a remote child
branch as part of this lifecycle.

Removal validates released resources before outer cleanup and again after exact
worktree/ref cleanup. It then atomically persists an exact removal-intent
snapshot, deletes the terminal receipt, deletes the ownership manifest, rescans
the snapshot's resources, and deletes the intent last. The intent remains a
publication claim and exact compare-and-set anchor across either deletion crash
window. Removal never deletes an active lease.

## Publication Classification

The primary and every linked checkout are classified by canonical path,
checked-out branch, and shared Git common directory against complete branch
metadata, all ownership manifests, active integration reservations, and all
lease resources. This prevents a private candidate ref moved into the primary
checkout from bypassing publication policy. Exact private matches are blocked.
Partial matches, malformed state, an absent resource that physically reappears,
or an unclaimed checkout inside the deterministic managed parent are
inconsistent and blocked. The primary source is allowed only when no private
claim matches; a manual linked worktree outside the managed namespace is
allowed under the same condition.
