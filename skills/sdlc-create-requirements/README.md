# Create Requirements

`sdlc-create-requirements` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Convert user intent into durable, testable product requirements in
`docs/requirements.md`, including an optional guarded Live Experiment
Environment section for later evaluation and UAT.

## Main Boundaries

- Edit `docs/design.md`.
- Create execution plans.
- Implement code or tests.
- Rename existing requirement IDs.
- Store raw credentials, private endpoints, customer data, or raw logs.

## Primary Inputs

- User prompt or approved change request.
- Existing `docs/requirements.md` when present.
- Existing `docs/design.md` for impact awareness only.
- Optional non-production or disposable live experiment environment details.
- Optional Jira, Slack, Confluence, GitHub, or pasted context.

## Output

- `docs/requirements.md` exists.
- Every requirement has acceptance criteria, validation method, test method, and evaluation method.
- Live Experiment Environment status is recorded.
- Open questions and change log are explicit.
