# SDLC Commit

`sdlc-commit` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Create a local commit for one completed feature as a durable checkpoint without pushing.

## Main Boundaries

- Push.
- Create PRs.
- Merge.
- Commit local run state.

## Primary Inputs

- Current feature ID.
- Validation, test, and evaluation evidence.
- Changed files.
- Current branch.

## Output

- Local commit exists or no-op is justified.
- Commit message references feature and requirement IDs.
- Commit evidence records commit hash.
- Feature state is `committed`.
