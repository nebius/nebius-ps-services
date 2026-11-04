# Review PR commands

Read-only inspection uses `gh pr view <pr-url> --json number,url,title,author,headRefName,headRefOid,baseRefName,baseRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`
and `gh pr diff <pr-url>`. Paginate API files/reviews/threads; a truncated diff or
missing checks never establishes review completion.

Metadata/diff-only review does not check out or create a local PR branch.
Use create-pr's private preparation handoff for required checkout, canonical commits,
base synchronization and explicit feature-branch pushes. Do not invoke raw Git
commit/merge/rebase, update-branch or force-push commands from this skill.

Follow [the Actions protocol](../../merge-pr/references/actions-protocol.md) for a passed
ordinary local review's exact-head COMMENT attestation. It is excluded from
report-only, non-default admission and active Agentic SDLC review mode.

Before outer return or safe pause, use the inherited
[checkout completion](../../create-pr/references/checkout-completion.md) policy.
Nested reviews return evidence without finalizing their parent.
