# Reconciliation Cases

Use these cases to evaluate root-agent prompt classification and paired
reconciliation. The hook records only metadata-only intake; the root agent
owns every semantic classification and repository write.
`trigger-prompts.csv` is the sole canonical trigger authority; these are
post-routing process cases. Contract tests remain required for advisory status
and output assertions; canonical CSV validation does not replace them.

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

## Mixed direct prompt

The user asks for a durable CLI behavior change and also asks an unrelated
question. Classify the CLI statement as lifecycle intent and the question as
non-lifecycle. Reconcile only the durable statement; do not force the whole
prompt into one category.

## Pre-implementation and delivery evidence

A new active requirement has an approved ready design, but no code exists yet.
Record design delivery as `not-started` and keep the requirement active. After
implementation, record implementation evidence. Advance to verified delivery
and a satisfied requirement only after focused verification evidence exists.

## Worker discovery

A worker discovers that its assignment cannot satisfy a mapped requirement
without changing the design boundary. It must not edit canonical specs or
reclassify user intent. It returns a typed `spec_gap`; the root coordinator
decides whether to reconcile and replan.

## Neither

The delta is formatting, tests, an internal refactor, or a bug fix that restores
the documented contract without changing architecture. Keep both documents
byte-identical and report validation status without gating ordinary work.

## Reverted

An implementation experiment is fully reverted before reconciliation. Classify
the final tree, not intermediate tool events; keep both documents unchanged
when no semantic delta remains.

## Ambiguous

The final diff could alter an observable contract or design boundary, but the
accepted intent or implementation evidence is incomplete. Do not infer or skip
the impact. Report a pending advisory and request the missing decision or
evidence without blocking an independent workflow.

## Missing project instructions

The selected project has no `AGENTS.md`, and bounded repository evidence shows
that no durable project-specific agent rule remains outside the canonical specs
and reusable skills. Report `not-needed` as a recommendation; leave the missing
file absent. Project-instruction mutation remains a separate explicit workflow.

## Existing users require compatibility

The user says that people already use the project and future changes to code or
interfaces must remain safe, without using `GA` or `backward compatibility`.
Treat the meaning as explicit compatibility intent. Update requirements and
design to protect supported public APIs/imports, CLI behavior, configuration
schemas/defaults, persisted formats, and upgrade paths. Require explicit
approval, migration or deprecation planning, and regression coverage for a
breaking supported change while keeping private internals on one canonical
path. Recommend the separate explicit project-instruction workflow when durable
rules are needed, but do not make rendering or reload an implementation gate.
Do not let a personal global no-compatibility default suppress the project
decision.

## Compatibility intent is ambiguous

The user mentions users, maturity, or future safety without saying that current
supported behavior or interfaces must remain compatible. Do not promote a
guess into durable project policy. Ask for the missing decision and report the
project-instruction recommendation as pending advisory context.
