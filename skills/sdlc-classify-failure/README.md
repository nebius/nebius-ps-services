# Classify Failure

`sdlc-classify-failure` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Normalize failures, validate optional troubleshooting diagnoses, enforce
repair budgets and invalidations, and select the shortest evidence-backed owner
or stop route before any SDLC retry.

## Main Boundaries

- Retry without classification.
- Overwrite old failure history.
- Persist raw secrets, logs, or private data.
- Route to merge or PR creation.
- Route an ambiguous evaluation failure directly to implementation or design.
- Treat missing evidence or failure to find an implementation bug as design
  proof.

## Primary Inputs

- Current feature and phase.
- Failure evidence.
- Retry counts.
- Latest validation, test, evaluation, documentation, UAT, PR, or policy
  output.

## Output

- Immutable failure, diagnosis, design-approval, and classification records
  are stored in private run state.
- The deterministic repair-control helper records the stable blocker, route,
  invalidations, attempts, ceilings, and diagnosis pointer under one
  process-safe per-feature transition lock.
- Known mechanical owners bypass troubleshooting; ambiguous evaluation
  failures enter it once and every diagnosis returns through classification.
- The next owner or precise stop condition is explicit.

## Deterministic Helper

Use `scripts/repair_control.py` for `record-failure`, `record-diagnosis`,
`record-approval`, `classify`, `dispatch`, `complete`, and
`record-revalidation`. A broader redesign is admitted only when
`record-approval` has stored an immutable
`design-approval-v1` bound to the exact event, normalized design gate, and
digest-verified explicit approval excerpt in a private user-input snapshot. The
helper derives `failure-log.md` from structured records and fails closed on
duplicate drift, malformed or sensitive evidence, unsupported redesign,
repeated direct repair, semantic
cycling, and exhausted blocker or feature budgets.

A successful repair with invalidations enters `revalidation_required`.
`record-revalidation` advances one ordered gate at a time using immutable
`revalidation-evidence-v1` whose source must be a structured phase-owned
`passed` result. The helper verifies its file digest, current workflow
fingerprints, immutable classification, successful dispatch, and cursor
identity. Pre-commit evidence must match the live integration HEAD. Final
commit evidence must match completed promotion, the clean promoted checkout
on its recorded base branch, and verified integration cleanup. The helper
records `resolved` only after the cursor has cleared every invalidated surface.
