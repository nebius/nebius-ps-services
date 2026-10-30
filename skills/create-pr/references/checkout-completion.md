# Local checkout completion

This is a private create-pr handoff to the canonical installed commit transaction,
not a public flag or a second Git implementation. Read it before ordinary PR
intake, checkout-dependent review, or returning from a PR batch. Report-only,
nondefault admission, already-merged observation and Agentic SDLC limits
remain authoritative. An invocation that only observes an already-merged PR
starts no local mutation transaction.

## Immutable policy and ownership

Resolve the actual default branch before intake; never assume its name. Begin
before any branch movement, with the original receipt and the complete selected
target set. Ordinary PR work uses the private `--checkout-policy ordinary`
default. Publication callers explicitly use `--checkout-policy preserve`; this
keeps their prepared checkout (the new feature when starting from default) and
never refreshes local branches as a side effect of nested PR handling. The grant
freezes the original ref/head, existing local default identity and intended
final ref separately from publication targets. The default remains prohibited
as a publication target. Resume uses the saved grant and targets, never the
current checkout as a new starting context.

Review metadata and the complete diff without checking out or creating a local
PR branch when no checkout-dependent work is needed. Begin a canonical grant
before an eligible merge even when the PR is remote-only and needs no repair.
A zero-effect remote target may stay absent locally through closure. For local
validation or repair, call private `checkout-target --branch <selected-branch>`
with the existing task key. It materializes only the captured selected target
and switches safely. A new feature at the receipt HEAD may carry the existing
dirty work; all other switches require clean state. Never use raw branch
creation, gh checkout, reset, stash, rebase or file-restoration commands here.

## Completion and safe pause

All calls below use the original `--repo-root`, `--session-id`,
`--requested-action create-pr` and `--intent-sha256` task key.

- `complete-checkout --mode pause`: switch back to the original branch without
  integrating history; publication policy keeps its declared prepared checkout.
  Keep the grant open. On resume, `begin` with omitted scope reuses saved targets
  and `checkout-target` returns to the required unfinished target.
- `complete-checkout --mode record-merge --merged-target <branch>
  --result-sha <authoritative-result>`: the merge-pr owner supplies the confirmed,
  repository/head/base-bound result, including squash/rebase result identity.
  Record it immediately after confirmation, before CI/Pages observation. This
  irreversible target checkpoint prevents another repair, synchronization,
  push or merge of that PR; it is not a CI success assertion.
- `complete-checkout --mode finalize`: at the outer return boundary, verify
  local state, fetch the frozen destination, independently verify result
  containment, fast-forward eligible existing original/default refs and switch
  back. Creation from default without a merge leaves its new feature checked
  out; review or visits to existing targets switch back without integrating.
  Publication policy preserves its declared prepared checkout and local refs.
- `finish --outcome completed`: close only after local completion and the
  selected workflow boundary has a terminal outcome. A conclusive CI/Pages
  failure remains a reported verification failure even when the local transaction
  closes; pending verification keeps its grant open. The helper validates final checkout,
  each update/skip and publication checkpoints. Explicit branch deletion happens
  after closure under its existing authority, retaining checked-out branches.

Nested create/review/merge calls return evidence to the outer owner and never
finalize or close the caller independently. A batch finalizes only on outer
return; an original branch that remains an unfinished publication target cannot
advance. For resumable unfinished batch work use temporary switch-back. Pending
merge settlement or CI keeps the grant open; once all merge effects are settled,
local finalization can run even when exact-result CI/Pages fails or times out.
A CI-only continuation observes the saved result; it never repairs or merges
that PR again. Terminal local completion does not imply successful verification.

Divergence or unique local commits produce a preserved-history skip. A missing
local default stays absent. Missing original refs, dirty/staged/untracked work,
active Git operations, ignored collisions, unexpected ref movement, ownership
conflicts and unfinished effects remain visible blockers. Fetch/result proof
failure permits safe switch-back with branch updates explicitly unavailable.
No implicit merge, rebase, reset, branch recreation or force switch is allowed.
Reconcile a retained effect by replaying the same owner command. Never edit its
private journal or close it merely to evade a failed switch.

## Reporting and installation

Report separately: merge outcome and authoritative result; destination proof;
exact-result CI and Pages; final checkout; each local ref's before/after and
updated, unchanged, diverged, missing, unfinished, owned elsewhere or unavailable
outcome; grant open/closed; explicitly requested deletion. Local completion
cannot convert failed verification into success or authorize publication.

Task, checkpoint and closure schemas upgrade together. Resolve active old
transactions with their existing installed owner before upgrading. Preserve
historical evidence without conversion, compatibility shims or reopening closed
grants. Validate source and installed behavior in disposable native homes;
quiesce new transactions before installation rollback. Source, installed,
native-agent and live GitHub evidence remain distinct.

Git documents [conditional ref updates](https://git-scm.com/docs/git-update-ref),
[safe switching](https://git-scm.com/docs/git-switch), and
[fast-forward-only integration](https://git-scm.com/docs/git-merge). The transaction
freezes the advertised destination SHA and fetches that object without using
shared FETCH_HEAD as evidence. It checks worktree occupancy before and after
effects and journals exact ref and
checkout results separately from reviewed publication heads.
