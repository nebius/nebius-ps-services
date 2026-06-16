# Merge PR

`sdlc-merge-pr` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Merge only after explicit human instruction and final readiness verification.

## Main Boundaries

- Merge without explicit user request.
- Override branch protection.
- Force merge.
- Merge failed UAT.

## Primary Inputs

- Explicit user merge request.
- PR URL or number.
- Desired merge method if specified.

## Output

- PR is merged or blocker is reported.
- Merge evidence is stored.
- SDLC run is marked complete when applicable.
