# Private root-task lifecycle

These are coordinator implementation details, not public skill flags. Use the
canonical installed `commit/scripts/commit_transaction.py`, native session ID,
exact repository root and hook-provided receipt digest. Never manufacture a
receipt or write private authority records. Explicit root-user intent supplies
authority; the receipt proves its origin. Delegated Worktree and Task Implementer
callers retain their existing interfaces and schemas.

## Intake and attempts

Call `begin --repo-root <root> --session-id <session> --requested-action
<commit|commit-push|create-pr> --intent-sha256 <receipt-digest>` before any branch
switch or HEAD movement, even when initially clean. The receipt must still
match the checkout at intake. Repeated identical begin is idempotent. Unrelated
later prompts do not replace this task key. Begin returns the canonical
`authorization` path and a `claims` map keyed by each selected full ref. Use
that target's returned path after switching; the original hook's claim path
belongs only to its intake branch. Never construct private paths manually.

For PR tasks add `--pr-base <base>` and one `--target <branch>` per selected
branch. Freeze the whole target set before switching: local refs, remote-only
refs, or a planned feature ref when starting on the default branch. Create a
planned feature at the receipt HEAD; create a remote-only local branch at its
captured remote SHA. The helper verifies that checkpoint before any commit.
Optional repeated `--dependency <child>:<parent>` freezes an acyclic dependency
map. `--validation-branch <new-temporary-branch>` reserves a non-publication ref
for ordered validation. Scope cannot be expanded on retry.

Only standalone commit supports `--allow-default-branch`, after explicit user
authorization for that branch. Publication tasks reject default-branch targets.

Use `prepare --repo-root <root> --session-id <session> --authorization
<canonical-path> --claim <canonical-path> --requested-action <action>
--intent-sha256 <original-receipt-digest>` for each candidate. Review its complete
temporary-index tree, then pass its one-shot token and exact reviewed tree to
`execute`. Each attempt has immutable authorization evidence, independent from
the session's current preparation slot. Returning from PR branch B to branch A
therefore preserves A's earlier evidence.

A failed hook or changed candidate that provably created no commit may be
corrected within existing task scope, re-prepared and reviewed without another
user invocation. Standalone commit and commit-push allow one actual commit.
A hook-modified commit consumes that allowance and requires exact `review`;
it does not authorize another commit. PR tasks allow successive validated
repairs at each selected target checkpoint. Tokens never carry to a changed
candidate. Commit-push may retry pushing its verified commit without receiving
another local-commit allowance.

## PR synchronization and order checks

Use `sync` with the same root/session/action/receipt arguments to merge the
captured forward-moving base into the current selected target. For a declared
dependency add `--dependency <parent-branch>`. The helper records exact source
and destination SHAs before normal-hook Git execution. It accepts only the
recorded no-op, fast-forward or correctly oriented two-parent result.

Review the returned actual tree and run affected checks, then repeat `sync
--reviewed-tree <tree>`. A conflict retains one pending operation. After an
authorized resolution, call `sync --continue` to preview the complete candidate
without changing the real index. Review the returned `candidate_tree`, then
use `sync --continue --reviewed-tree <candidate-tree>`; inspect a hook-altered actual result
again before acknowledging it. Do not use ordinary single-parent claims for
merge commits. Do not execute raw merges or infer authority from arbitrary
ancestry. A clean unchanged not-started checkpoint permits replay after a
helper crash; uncertain effects remain blocked for reconciliation.

For multiple branches, use `validate-order` with the same task arguments and
one `--branch <target>` per target in the declared dependency order. It uses
only the reserved scratch ref and verified source checkpoints, records merge
progress, restores the exact original checkout and deletes the scratch ref by
its expected SHA. It never publishes that ref or modifies target histories.
Conflicts and interrupted cleanup retain ownership evidence. Unexpected
checkout/ref changes require reconciliation, never blanket reset or cleanup.

## Completion, cancellation and recovery

Call `finish` with the same task arguments and `--outcome completed` after the
local task or publication is verified, including zero-commit workflows.
Completion requires clean verified target heads and no pending claim, merge
or scratch cleanup. Use `--outcome cancelled` when abandoning the task.
Cancellation terminalizes proven unused claims and retains actual or uncertain
effects. Exact result review after cancellation is metadata-only and cannot
reopen Git execution. Closed tasks require a fresh authorized task for new work.

Root grants, root authorizations and root claims use their new schemas; old
root records are never converted into authority. An explicitly authorized new
standalone task may adopt only supported exact interrupted new-schema evidence.
Delegated schemas remain unchanged. Old terminal records remain historical;
active old records must be resolved through their owning version/workflow.

The common-repository lock is inherited by Git children. Reconciliation waits
for surviving writers, so helper death is not proof that an operation stopped.
Errors include `code`, `retryable` and `next_action`: retry only a proved
no-commit candidate failure; reconcile review/history/ownership failures at
their owner. Secret hazards, unknown effects, SDLC ownership and required human
GitHub approval remain boundaries. Task metadata cannot bypass them.
