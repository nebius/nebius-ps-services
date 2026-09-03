# Maintain Project Specs

`maintain-project-specs` owns the canonical requirements/design contract for
ordinary root-agent sessions, Task Implementer, and Agentic SDLC. Every direct
root-user prompt is classified at statement level. Durable product intent is
recorded before implementation, and proven implementation/verification
evidence is recorded afterward. Unrelated requests leave the specs unchanged.

Hooks remain advisory because a hook cannot safely make the semantic decision
or perform a repository transaction on behalf of the active workflow. They
only observe the actual documents and stage metadata-only intake. The root
agent performs classification and calls the canonical owner.

## What Advisory Means

- SessionStart uses bounded, no-follow, owner-controlled regular-file reads. It
  is silent for a current v2 pair and may report `CONTRACT_PENDING`,
  `CONTRACT_INVALID`, or `CONTRACT_MIGRATION_REQUIRED` based on the actual
  document markers.
- UserPromptSubmit stages only prompt/session/turn digests and an unclassified
  intent record, then injects bounded classification guidance. Raw prompt text
  is never persisted.
- Subagents, workers, generated continuations, system/compaction turns, Stop
  turns, and secret-bearing prompts are excluded from intake.
- Project-spec PreToolUse, PostToolUse, and Stop registrations do not exist;
  the project-spec delegate is absent from shared Stop arbiters.
- Missing state, unsafe paths, or internal exceptions degrade to
  advisory/neutral behavior and never recreate the old
  `ADVISORY_UNAVAILABLE` lifecycle-state warning.
- Task Implementer and Agentic SDLC keep their own execution and safety gates.
- Project-instruction changes require a separate explicit workflow and are not
  part of automatic lifecycle maintenance.

Existing repository instructions still apply. Advisory lifecycle status does
not bypass security, destructive-action, Git, external-write, or workflow-owned
controls.

## Canonical Documents

The skill understands canonical managed regions in:

- `docs/requirements.md`
- `docs/design.md`

Schema v2 separates requirement state (`draft`, `active`, `blocked`,
`satisfied`, `superseded`), design readiness (`draft`, `ready`, `blocked`,
`stale`, `superseded`), and delivery (`unassessed`, `not-started`,
`implemented`, `verified`). A ready design may be not started; a satisfied
requirement must have verified delivery. The owner preserves human-owned bytes
outside managed regions, stable record IDs, status-aware traceability, and
marker/body agreement.

The paired publisher validates both documents and compare-and-set checks Git
`HEAD` plus both prior document digests before writing either file. Publication
holds verified no-follow project/docs directory descriptors, rechecks the Git
and file boundary after each replacement, and never follows a swapped `docs`
path. It journals digest-only recovery metadata, restores only bytes still
equal to its candidate if a later step fails, and safely completes a
digest-proven before/after split on an exact operation replay. Owner-aware
inspection uses the same shared lock and therefore observes one complete pair;
ad hoc direct reads of the two files are not a transaction API. It never stores
prompt text, transcripts, repository content, secrets, customer data, or raw
logs in private lifecycle state.

## Helper Commands

```text
python3 scripts/project_specs.py inspect --project-root <path>
python3 scripts/project_specs.py validate --project-root <path>
python3 scripts/project_specs.py publish --project-root <path> \
  --requirements-candidate <path> --design-candidate <path> \
  --expected-head <sha> \
  --expected-requirements-sha256 <sha-or-absent> \
  --expected-design-sha256 <sha-or-absent> --operation-id <id>
python3 scripts/project_specs.py migrate --project-root <path>
python3 scripts/project_specs.py recover --project-root <path>
```

Inspection and validation are advisory. `publish` is the normal paired
compare-and-set write path. Migration and recovery are explicit mutating
operations with journal/backup safety. Lifecycle state-transition commands
(`start-prompt`, `plan`, `open`, `seal`, and `waive`) are retired.

## Hook Sources

The optional source bundle lives under `assets/hooks/`:

- `project_specs_lifecycle.py`: document observer and metadata-only prompt
  intake for root-agent classification.
- `project_specs_maintenance.py`: private retention and pressure housekeeping.
- `stop_lifecycle_arbiter.py`: peer Stop arbitration without a project-spec
  delegate.

Source edits are not live activation proof. Validate source behavior, install
into a disposable Codex home, start a fresh process/session, and verify neutral
hook behavior separately.

## Retention

Private retention remains fail-safe and non-authoritative:

- current-session bytes count toward aggregate pressure but the current session
  cannot be deleted;
- valid terminal state is retained for 30 days;
- abandoned resumable state is retained for 90 days;
- pressure cleanup uses 1 GiB/768 MiB allocated-storage hysteresis only when
  registry and aggregate evidence are complete;
- SessionStart resolves only the exact source-sibling or canonical installed
  ownership classifier, validates the complete owner-controlled package path
  and source set, copies only snapshotted bytes into an owner-only temporary
  tree, verifies source identity and digests before and after an isolated
  bounded invocation, and accepts only its exact read-only result schema;
- incomplete, malformed, foreign, digest-mismatched, pending, or unsafe state
  disables pressure deletion, and any missing, spoofed, failed, timed-out, or
  malformed classifier result—or classifier tree changed during execution—is
  treated as `unsafe`;
- lock order, owner-only modes, rename journaling, and no-follow deletion remain
  strict.

## Workflow Independence

Task Implementer and Agentic SDLC use the same v2 parser, pair validator,
publisher, and receipt. Their prompt-impact schemas, plan bases, dispatch,
cleanup, finalization, recovery, and Stop behavior remain workflow-owned. Root
coordinators bind the accepted intent and exact spec receipt before dispatch.
Workers inherit that evidence, never reclassify user intent or write canonical
specs, and report typed `spec_gaps` to the coordinator for reconciliation.

## Validation

Focused tests cover:

- current, pending, invalid, migration-required, and failure-safe hook behavior;
- metadata-only/idempotent direct-prompt intake and worker/generated/secret
  exclusions, including serialized concurrent delivery of one turn;
- absence of project-spec PreToolUse, PostToolUse, and Stop registrations;
- absence of the project-spec Stop delegate;
- current-session allocated-byte accounting and retention protection;
- real SessionStart reachability of ownership-safe age cleanup and fail-safe
  classifier degradation, including writable ancestry, source replacement,
  and partial registry proof;
- foreign project-scope evidence isolation;
- canonical v2 pair validation, directory-swap-safe compare-and-set
  publication, owner-aware reader serialization, guarded rollback, and explicit
  v1/legacy migration recovery;
- Task Implementer and Agentic SDLC adapter parity and workflow independence.
