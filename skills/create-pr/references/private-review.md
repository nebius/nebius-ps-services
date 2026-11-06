# Private PR review execution

Read this before checkout-dependent review in any standalone, create-pr,
merge-pr or publication call. Metadata/diff-only review allocates nothing. Help
stops before intake. Explicit report-only and active SDLC restrictions remain;
this contract never grants repair or merge authority to those modes.

## Source and execution ownership

The caller checkout is read-only throughout review: no switching, checkout,
fetch into its refs, staging, stash, reset, clean, synchronization or automatic
adoption of repair commits. Concurrent user edits remain user work. Inspect
GitHub's selected PR rather than copying local dirty or unpublished work.

Use the trusted installed commit transaction and its sibling review workspace
module. The original receipt/grant remains the authority. An execution manifest
binds exactly one selected PR to that original owner; it cannot act independently
or after original closure. Do not copy receipts, manufacture session identities,
call root begin in the private repository, or reuse an arbitrary existing clone.
The owner serializes original-repository then execution-repository locks, retains
both in Git children and fences parent mutations of delegated targets.

The owner initializes an independent repository under
`<agent-home>/review-workspaces/<operation>/<target>/repo`, with private ancestor
directories, an empty init template and exact remote base/head fetches. It copies
only author identity, never caller configuration, repository hooks, dirt or shared
object stores. It pins the source's effective hook policy and requires no active
executable hooks before using its own empty hook directory. Executable hooks block
private allocation/repairs until restricted hook execution is available: a trusted
launcher can still write beside its source script or execute candidate programs
with operator access. Never silently bypass required hooks or claim they passed.
Changed, linked, unsafe or unbounded policies also fail closed. Default and origin
identity remain separately bound. Fork aliases
remain `pr/<base-id>/<number>/<operation-id>` inside this private repository.

Before each private Git process, reject executable filters, fsmonitor commands
and external diff/merge drivers, including conditional configuration. The sole
exception is unused LFS configuration: the owner forces empty LFS clean, smudge
and process commands with `required=true` for every private invocation. It checks
effective attributes in exact incoming trees and working/index candidates.
Actual LFS-managed paths stop with `unsupported_lfs`; no LFS binary executes and
no content is downloaded, uploaded or silently skipped. Caller configuration is
unchanged. Full LFS support remains deferred.

Private checkout isolation is not contributor-code execution isolation. Keep
trusted operator controls and credentials outside candidate execution. Run tests
in the restricted disposable environment required by contribution assessment,
without writable caller/trusted-state access. If that boundary is unavailable,
report validation unavailable. Never run candidate code in the agent home or
fall back to the caller checkout.

## Standalone review

For authorized repair-capable review, use the original source receipt and native
session with private owner intake:

```text
python3 <commit>/scripts/commit_transaction.py begin-review \
  --repo-root <caller-root> --session-id <native-session> \
  --requested-action create-pr --intent-sha256 <original-receipt-digest> \
  --pr-base <selected-base> --target <local-target> \
  --pr-bindings <private-selector-json>
```

Use the bound selector shape from [PR publication](pr-publication.md). Intake
returns `task` for the original owner and an `executions` entry per selected ref.
Each entry supplies `repo_root`, subordinate `task`/digest, authorization and claim
paths, branch, head and base. The source need not be clean or at the remote PR
head. Allocation failures never authorize switching the caller.

Use the returned execution root and claim/authorization paths for existing
`prepare`, `execute`, `sync` and `publish-target` operations, retaining the original
session, requested action and receipt digest. Stage only the private repository
root through the canonical owner. Renew validation and complete-diff review after
head/base changes; preserve current permission and exact remote-head checks.

## Nested review

An existing create/merge/publication owner calls private `open-review` with its
original task arguments and `--branch <selected-target>`. If that grant predates
PR creation, also provide `--pr-bindings <private-selector-json>` for that one
published target. The remote head must equal the parent's verified checkpoint.
The original owner cannot mutate that delegated target afterward; route its PR
continuation through the returned execution context. Repeated calls reuse and
revalidate the registered context rather than minting another root authority.

Return the execution manifest path, final head/base, publication evidence and
review ID to the caller. The read-only private `review-context` operation, using
original owner arguments plus `--branch`, verifies a clean published execution.
Release `bind-pr` consumes `--review-task <execution-manifest>` when review used
private execution; repaired-head `reconcile-pr` requires it. Keep the original
release project path/store identity. Image/Helm handoffs carry the same context.
The canonical proof checks private branch, cleanliness and previous-head ancestry
under its lock. Release consumers must not issue separate unguarded private Git
commands after that proof. No consumer may infer a repaired head from caller refs.

## Completion, recovery and cleanup

Only the original owner closes the operation at its selected boundary. Standalone
review uses original-root `complete-checkout --mode finalize` followed by
`finish --outcome completed`; source preservation performs no Git mutation and
reports no asserted final checkout. Parent workflows retain separately authorized
checkout effects: an ordinary parent that visited another branch must still
complete its own switch-back, and user dirt can block that parent effect without
invalidating a completed remote review repair. Publication preserve policy keeps
its prepared checkout. Review never imports its repair head into the source.

Pause retains the original grant and execution. Resume uses the same original
arguments and registered repository. Reconcile unknown pushes before retrying;
changed remote head/base invalidates review. Allocation interruptions reuse only
the exact owned directory and recorded scope. Cancellation can close an incomplete
allocation that never acquired execution authority; preserve its files for owner
inspection. Unresolved remote effects block closure.

After original terminal closure, call private `cleanup-review` with original
arguments and `--branch`. It verifies clean published work, exact allocation and
repository identities, and inherited-lock quiescence before journaled deletion.
Unpublished repairs, uncertain effects, symlinks or substituted directories are
retained with a blocker. Interrupted deletion resumes only for the bound directory
identity or settles proven absence. The owner retains transaction evidence.
Report the retained path/reason and cleanup responsibility when deletion is blocked.

Task/checkpoint/closure schemas remain v4. Old records are historical evidence,
not a blanket block on new v4 work. Resolve active old operations with their
original owner; otherwise use the [administrative audit](../../commit/references/transaction-retirement.md).
Never translate grants, infer abandonment from age, or replay old closure authority. Validate source and disposable native installations separately.
These are private helper operations, not new public skill actions or flags.
