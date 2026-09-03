# Project-Spec Intake and Reconciliation Lifecycle

The project-spec lifecycle keeps durable product intent and delivered design
evidence current. It is not an authorization state machine: the root agent
owns semantic classification and repository mutation, while hooks only observe
documents and stage metadata-only intake.

## Owned Artifacts

- canonical managed regions in `docs/requirements.md` and `docs/design.md`;
- optional project policy in `.codex/project-specs.json`;
- private prompt-intake metadata, validation receipts, locks, journals, and
  retention metadata under `CODEX_HOME`.

Project instruction files are outside this lifecycle. A separately invoked
`project-agent-instructions` workflow owns any requested mutation.

## Canonical State

Requirement state, design readiness, and delivery are independent:

| Axis | Values | Rule |
| --- | --- | --- |
| Requirement | `draft`, `active`, `blocked`, `satisfied`, `superseded` | `satisfied` requires covering verified delivery. |
| Design readiness | `draft`, `ready`, `blocked`, `stale`, `superseded` | Implementation starts only from a covering `ready` design. |
| Delivery | `unassessed`, `not-started`, `implemented`, `verified` | Implementation and verification evidence are recorded separately. |

Pair inspection reports `current` or `pending`; structural defects have exact
validation codes. No status admits or denies a tool or workflow action. Old
phase names such as `planning-required`, `planned`,
`implementation-open`, `reconciliation-required`, `seal-armed`, `sealed`, and
`waived` are historical data only.

## Hook Behavior

### SessionStart

- Resolve only the exact current project scope and inspect actual canonical
  markers rather than private lifecycle state.
- Honor `mode: disabled` with no lifecycle message.
- Stay silent for a current v2 pair; otherwise emit at most one bounded
  `CONTRACT_PENDING`, `CONTRACT_INVALID`, or
  `CONTRACT_MIGRATION_REQUIRED` message.
- Do not create state merely by observing a project.

### UserPromptSubmit

- Apply only to direct root-user turns. Skip subagents/workers, system turns,
  compaction, generated continuations, Stop turns, and secret-bearing prompts.
- Stage one idempotent owner-private `prompt-intake.v2` record containing only
  scope, prompt/session/turn digests, and `classification: unclassified`.
- Inject bounded guidance requiring the root agent to classify each statement
  and reconcile durable intent before implementation. Never persist raw input.

### No tool or Stop hooks

- The project-spec manifest registers neither PreToolUse nor PostToolUse.
- It registers no Stop handler, and shared Stop arbiters omit the project-spec
  delegate entirely.
- Peer security and workflow hooks continue to evaluate independently.

### Failure

Exceptions, malformed input, missing state, and unsafe private paths degrade to
neutral/advisory behavior. They never produce a deny decision or
`continue:false`. Because intake is best effort, an interrupted root turn may
end before classification or repository publication; report that limitation
instead of inventing completion evidence.

## Root-Agent Reconciliation

1. Explicit project scope in the direct user prompt wins; otherwise use the
   SessionStart cwd. Ask when the choice remains ambiguous.
2. Classify every statement as durable requirement intent or non-lifecycle.
   Explanations, brainstorming, status, workflow control, one-off operations,
   formatting, and contract-restoring fixes are normally non-lifecycle.
3. Before implementation, reconcile durable intent into active requirements
   and covering ready designs with delivery `not-started`.
4. Publish both files through one compare-and-set transaction bound to Git
   `HEAD` and both predecessor digests. Hold verified no-follow project/docs
   directory descriptors, recheck the bound state after each replacement, and
   restore only an unchanged candidate on rollback. Owner-aware inspection
   takes the paired shared lock; unrelated direct file reads are not snapshots.
5. After implementation and focused verification, update implementation and
   verification evidence, then advance delivery and requirement status.
6. Workers inherit root intent and the exact pair receipt. They do not
   reclassify prompts or write specs; they return typed `spec_gaps` for root
   review.

## Canonical Validation

Inspection and validation remain useful quality tools. They may report:

- missing or malformed managed markers;
- duplicate/invalid stable IDs;
- unsupported statuses;
- marker/body disagreement;
- active requirements without current design coverage;
- stale implementation evidence;
- unsafe or mixed legacy ownership.

These findings stop only the explicit spec mutation or migration that cannot be
performed safely. They do not stop unrelated implementation, workflow cleanup,
Stop, or session completion.

## Workflow Boundaries

Task Implementer and Agentic SDLC own their accepted-intent, prompt-impact,
plan-basis, dispatch, resource, Git, validation, finalization, recovery, and
Stop decisions. Their root coordinators use the canonical parser, paired
publisher, and exact receipt for project truth without treating that receipt
as workflow authority.

Already-effective repository instructions remain authoritative. Automatic
workflow phases do not create, update, retire, apply, verify, or reload project
instructions.

## Historical State

Historical state remains non-authoritative and may be inspected for diagnosis.
Do not forward-migrate phase authority, synthesize completed receipts, or add
compatibility commands. Current behavior ignores historical phase data.

## Retention Safety

Retention is silent private housekeeping:

- count allocated bytes rather than logical bytes;
- include the current session in aggregate pressure accounting but never
  delete it;
- retain valid terminal bundles for 30 days;
- retain abandoned resumable bundles for 90 days;
- use 1 GiB high-water and 768 MiB low-water thresholds;
- resolve only the exact source-sibling or canonical installed read-only
  ownership classifier, validate its complete owner-controlled package path
  and imported source set, copy only those snapshotted bytes into an owner-only
  temporary tree, verify file identities and digests before and after an
  isolated bounded subprocess, and accept only its exact result schema;
- require complete exact-schema aggregate and canonical registry evidence
  before pressure cleanup;
- disable cleanup for incomplete, malformed, digest-mismatched,
  pending-publication, legacy-required, unsafe, or unknown evidence;
- classify a candidate as `unsafe` when the canonical classifier is absent,
  spoofed, linked, unowned, writable by other users, failed, timed out, or
  returns malformed output, and when any validated directory or imported
  source changes during execution;
- treat another selected-project scope as foreign rather than pending evidence;
- preserve stable lock order, owner-only permissions, fsynced same-device
  rename journaling, directory-FD-relative no-follow deletion, and exact
  current-session protection.

The maintenance cursor may rotate bounded inactive-workspace slices. Visit
counts bound work; they never authorize deletion by themselves.

## Source, Install, and Activation Proof

Keep four claims separate:

1. Source tests prove repository behavior.
2. Disposable installation proves source/installed parity.
3. A fresh process/session proves hook discovery and loading.
4. A real ordinary tool/Stop sequence proves neutral runtime behavior.

Never claim live activation from source parity alone.
