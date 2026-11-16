# SDLC Auto Steering

`sdlc-auto-steering` is an Agentic SDLC skill. It is authored in this
repository and is installed into a Codex runtime only when `install-skills.sh`
is run.

## What It Does

Refreshes private active-run steering by recording accepted same-objective revisions,
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
- Accepted objective ID, revision, digest, and immutable snapshot.

## Output

- `STEERING.md` and `steering/auto-steering.json` are current.
- Every unresolved steering entry has a disposition.
- Compact active reminders are ready for `sdlc-start`.

The coordinator issues `workflow/steering-readiness-v1` through the private shared
transition helper after reviewing `steering-inputs`. Missing/stale readiness is
pending work, not a repair. Unchanged inputs and timestamp-only ledger updates
converge; archived receipts and native ownership reject fabricated readiness.
The returned readiness command uses the authorized run's selected project as its
working directory, preserving the existing owned Git scope checks.
Only immediate scalar clock metadata of recognized owner records and steering
entries is excluded. Nested JSON schema fields, examples and unknown record
content remain bound, including fields named `updated_at` or `timestamp`.
