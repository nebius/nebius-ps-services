# Review PR commands

Read-only inspection uses `gh pr view <pr-url> --json number,url,title,author,headRefName,headRefOid,baseRefName,baseRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`
and `gh pr diff <pr-url>`. Paginate API files/reviews/threads; a truncated diff or
missing checks never establishes review completion.

Use create-pr's private preparation handoff for checkout, canonical commits,
base synchronization and explicit feature-branch pushes. Do not invoke raw Git
commit/merge/rebase, update-branch or force-push commands from this skill.

Follow [the Actions protocol](../../merge-pr/references/actions-protocol.md) for a passed
ordinary local review's exact-head COMMENT attestation. It is excluded from
report-only, fork/non-default admission and active Agentic SDLC review mode.
