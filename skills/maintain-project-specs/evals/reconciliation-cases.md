# Reconciliation Cases

Use these cases to evaluate the terminal semantic review. The hook records only
material-write evidence; the agent owns every classification.

## Requirements only

The user accepts a new observable constraint while the implementation continues
through an existing design boundary. Update requirements and confirm existing
design coverage remains accurate.

## Design only

The implementation moves persistence, changes component ownership, or changes
failure and recovery flow without changing accepted observable behavior. Keep
requirements unchanged and update design.

## Requirements and design

The user accepts a public interface or behavior change and implementation adds
or changes the boundary that provides it. Update both documents and preserve
traceability.

## Neither

The delta is formatting, tests, an internal refactor, or a bug fix that restores
the documented contract without changing architecture. Keep both documents
byte-identical, validate them, and re-plan at the latest write epoch.

## Reverted

An implementation experiment is fully reverted before reconciliation. Classify
the final tree, not intermediate tool events; keep both documents unchanged
when no semantic delta remains.

## Ambiguous

The final diff could alter an observable contract or design boundary, but the
accepted intent or implementation evidence is incomplete. Do not infer or skip
the impact. Keep the lifecycle unsealed and request the missing decision or
evidence.

## Missing project instructions

The selected project has no `AGENTS.md`, and bounded repository evidence shows
that no durable project-specific agent rule remains outside the canonical specs
and reusable skills. Record and verify `not-needed`; report that the missing
file remains absent. Do not describe this as a failed or skipped creation.
