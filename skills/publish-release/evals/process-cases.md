# Supplemental Process Cases

These cases preserve release-workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

## Quality Scenarios

For an explicitly requested complete release, verify that the result:

- from a clean synchronized default branch, creates `release/<tag>`, moves the
  non-empty `Unreleased` content into the dated release section, and uses that
  branch as the PR head;
- from a clean current feature branch that contains current default-branch
  history, updates, commits, and pushes that same branch without creating a
  nested release branch;
- pushes even a ref-like feature-branch name to the exact remote head namespace
  and never turns it into a tag;
- reuses an existing matching feature-branch PR instead of opening a duplicate;
- stops before changelog mutation for a dirty or detached checkout, stale
  default-branch history, remote feature-branch divergence, empty release
  payload, or a duplicate tag;
- returns to the clean synchronized default branch after the prep PR merges and
  refreshes its exact remote-tracking ref independently of local fetch config,
  then verifies the dated changelog section before creating the annotated tag;
- creates the annotated tag locally before checking an SCM-derived runtime
  version, pushes it only after the version matches, and removes that exact
  unpushed tag when verification fails;
- never tags from a feature branch and never bypasses checks, reviews, merge
  queues, branch protection, or human approvals;
- waits for and verifies the tag-triggered GitHub Release workflow and expected
  assets when the request uses the default wait behavior.

## Manual Runtime Check

In a fresh Codex surface where the updated source skill is installed, run one
complete request from a clean synchronized default branch and one from a clean
current feature branch in disposable repositories with local-only remotes.
Confirm the selected prep branch and every stop condition above. Until that
fresh-surface check is observed, report only static and deterministic helper
evidence, not runtime activation or live GitHub publication proof.
