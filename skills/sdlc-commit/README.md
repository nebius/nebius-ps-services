# SDLC Commit

`sdlc-commit` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Seal any final integration-only changes, fast-forward the unchanged project
feature branch to the exact verified integration tip, and non-force-clean the
integration resource without pushing.

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
