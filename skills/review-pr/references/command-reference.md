# Review PR commands

Read-only inspection uses `gh pr view <pr-url> --json number,url,title,author,headRefName,headRefOid,baseRefName,baseRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`
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
