# Review PR commands

Read-only inspection uses `gh pr view <pr-url> --json number,url,title,author,headRefName,headRefOid,baseRefName,baseRefOid,isDraft,state,labels,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`
and `gh pr diff <pr-url>`. Paginate API files/reviews/threads; a truncated diff or
missing checks never establishes review completion.

Metadata/diff-only review does not check out or create a local PR branch.
Use create-pr's [private review execution](../../create-pr/references/private-review.md)
before checkout-dependent work. Its preparation handoff performs checkout,
canonical commits, synchronization and explicit PR pushes only in that execution root. Do not invoke raw Git
commit/merge/rebase, update-branch or force-push commands from this skill.

Follow [the Actions protocol](../../merge-pr/references/actions-protocol.md) for a passed
ordinary local review's exact-head COMMENT attestation. It is excluded from
report-only, non-default admission and active Agentic SDLC review mode.

Before outer return or pause, preserve the caller checkout and keep the original
owner's execution record. Nested reviews return exact execution/publication
evidence without finalizing their parent. No review switch-back is necessary.

## Single-PR resolution

After complete syntax validation, `--pr <number-or-URL-or-branch>` takes
precedence over `--label` and `--pr-batch`. Report their names once as ignored;
do not check the ignored label's existence, apply it, enumerate matching PRs,
or enforce batch dependencies. A valid batch size without a label is ignored
when `--pr` is present; malformed values still fail before discovery.

Bind a full URL to its exact host/repository/PR. Resolve a number or branch in
the unambiguous established GitHub repository. A branch must identify exactly
one PR across relevant open and terminal states; inspect candidates sufficiently
to detect branch reuse. Do not choose the latest or prefer an open PR over an
older closed one. Ask for a PR number or URL when ambiguous. Missing/inaccessible
targets stop with the actual reason; never fall back to current-branch or label
batch discovery. Preserve the existing positional and no-argument selection
rules; reject mixing a positional target with `--pr`, even if values agree.

Fetch live state and labels, then follow
[shared routing](pr-lifecycle.md#selection-and-live-label-routing) before any
dispatch or status change. A later label change never selects another PR;
refresh the route and preserve original authority and consumed budgets.

GitHub's [PR view interface](https://cli.github.com/manual/gh_pr_view) supports
number/URL/branch selectors and `state`/`labels` JSON fields. Use existing
capable connector access or the authenticated CLI fallback; this skill flag
is not a new flag to pass through to `gh`.

## Label batch discovery

Read [label batches](label-batches.md) before discovery. Use repository-scoped
GitHub GraphQL `pullRequests` with a structured `labels` array, `orderBy` creation
ascending, explicit states and cursors when the connector exposes those fields.
The issue skill uses the corresponding `issues` connection. Verify exact literal
label membership and object type on returned nodes. Do not use semantic search
as an exact selector or interpolate untrusted label text into queries or shell.
If the connector lacks these capabilities, disclose the existing authenticated
CLI API fallback. A lack of safe access leaves selection pending, not permission
to bypass authentication or enumerate an unrelated repository.
