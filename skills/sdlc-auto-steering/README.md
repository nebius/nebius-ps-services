# SDLC Auto Steering

`sdlc-auto-steering` is an Agentic SDLC skill. It is authored in this
repository and is installed into a Codex runtime only when `install-skills.sh`
is run.

## What It Does

Refreshes private active-run steering by recording accepted same-prompt revisions,
classifying them, and deriving compact reminders for the next SDLC phase.

## Main Boundaries

- Do not edit `docs/requirements.md` or `docs/design.md`.
- Do not choose the next phase directly; return routing input to `sdlc-start`.
- Do not use cron, daemons, or hooks as the authoritative steering engine.
- Store only compact redacted prompt summaries; do not persist raw prompt text,
  secrets, private endpoints, customer data, or raw logs.

## Primary Inputs

- Active run state.
- `STEERING.md`.
- Requirements, design, context, locked plan, fingerprints, and evidence.
- Accepted prompt ID, revision, digest, and immutable snapshot.

## Output

- `STEERING.md` and `steering/auto-steering.json` are current.
- Every unresolved steering entry has a disposition.
- Compact active reminders are ready for `sdlc-start`.
