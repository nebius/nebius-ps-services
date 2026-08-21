# Reconciliation Cases

Use these cases to evaluate the terminal semantic review. The hook records only
material-write evidence; the agent owns every classification.
`trigger-prompts.csv` is the sole canonical trigger authority; these are
post-routing process cases. Contract tests remain required for lifecycle and
output assertions; canonical CSV validation does not replace them.

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

## Existing users require compatibility

The user says that people already use the project and future changes to code or
interfaces must remain safe, without using `GA` or `backward compatibility`.
Treat the meaning as explicit compatibility intent. Update requirements and
design to protect supported public APIs/imports, CLI behavior, configuration
schemas/defaults, persisted formats, and upgrade paths. Require explicit
approval, migration or deprecation planning, and regression coverage for a
breaking supported change while keeping private internals on one canonical
path. Render those rules before implementation and choose `needed` unless
active same-directory project instructions already express the equivalent
contract. Do not let a personal global no-compatibility default suppress the
project decision.

## Compatibility intent is ambiguous

The user mentions users, maturity, or future safety without saying that current
supported behavior or interfaces must remain compatible. Do not promote a
guess into durable project policy. Ask for the missing decision and leave the
lifecycle unsealed when focused project evidence does not resolve the intent.
