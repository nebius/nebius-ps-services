# Create Requirements

`sdlc-create-requirements` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Convert user intent into durable, testable product requirements in `docs/requirements.md`.

## Main Boundaries

- Edit `docs/design.md`.
- Create execution plans.
- Implement code or tests.
- Rename existing requirement IDs.

## Primary Inputs

- User prompt or approved change request.
- Existing `docs/requirements.md` when present.
- Existing `docs/design.md` for impact awareness only.
- Optional Jira, Slack, Confluence, GitHub, or pasted context.

## Output

- `docs/requirements.md` exists.
- Every requirement has acceptance criteria, validation method, test method, and evaluation method.
- Open questions and change log are explicit.
