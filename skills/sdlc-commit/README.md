# SDLC Commit

`sdlc-commit` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Seal any final integration-only changes, fast-forward the unchanged project
feature branch to the exact verified integration tip, and non-force-clean the
integration resource without pushing.
The promotion precheck, ff-only merge, and postcheck run under the shared Git
common-directory lock. Cleanup removes the integration worktree first and then
deletes its branch only at the exact expected promoted SHA.
In a managed child, this is an inner local promotion only. The later outer
handoff returns the exact `$worktree integrate <generated-name>` command and
stops for a fresh explicit user invocation; the child is never pushed or used
as a PR head.

## Main Boundaries

- Ordinary local commits outside Agentic SDLC; use `commit`.
- Push.
- Create PRs.
- Merge.
- Commit local run state.
- Squash, rebase, amend, cherry-pick, force-delete, or promote a different SHA.

## Primary Inputs

- Current feature ID.
- Validation, test, and evaluation evidence.
- Changed files.
- Current branch.
- Execution coordinator and exact integration/base identities.

## Output

- Final integration commit exists or a clean no-op is justified.
- Project promotion is exact and fast-forward-only.
- Integration cleanup succeeds or remains explicitly blocked without force.
- Commit message references feature and requirement IDs.
- Commit evidence records commit hash.
- Feature state is `committed`.
