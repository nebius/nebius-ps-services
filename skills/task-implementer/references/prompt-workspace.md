# Task Implementer Prompt Workspace

The prompt workspace is private durable input and state for one exact project
scope. It is not repository content and does not grant lifecycle authority.

## Layout

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/projects/<project>/<scope>/
├── workspace.json
├── activity.json
├── prompt-queue.json
├── queued-prompts/
├── prompts/
│   ├── 00-START-HERE.md
│   └── *.md
└── runs/<run>/
    ├── manifest.json
    ├── steering.json
    ├── requirements-refinement.json
    ├── prompt-impact-claim.json
    ├── prompt-impact/
    ├── inputs/<revision>/prompt.md
    ├── handoff.md
    └── orchestration/
        ├── coordinator.json
        ├── resume-control.json
        ├── waves/
        ├── tasks/
        ├── assignments/
        ├── results/
        ├── evidence/
        ├── journals/
        ├── interop.json
        ├── run-summary.prepared.json
        └── run-summary.json
```

Directories are owner-only. State files are bounded regular files written
atomically with mode `0600`. Reject symlinks, unsafe ownership, hard-linked
mutable state, and path escape.

## Managed Prompt Contract

One prompt has a stable private ID and filename. Only `## Ask` is required;
optional headings may capture context, constraints, acceptance criteria,
verification, non-goals, assumptions, dependencies, and references.

An explicit `run` accepts one immutable revision snapshot. Editing the same
prompt and running again is steering. Exact retries do not append a duplicate
revision. Editing a completed prompt starts a linked new full-objective run;
omission never deletes previously accepted product truth.

`prompt-session-intake` may merge a safe project-intent projection into the
already-bound prompt. It excludes workflow instructions, commands, delivery
requests, agent-control text, status chatter, and sensitive material. Capture
never invokes Task Implementer, advances workflow state, or blocks the direct
request on failure.

## Workspace Identity

`workspace.json` binds:

- canonical Git common directory;
- exact primary checkout and named non-default source ref;
- exact selected project scope;
- persistent lane identity;
- private paths and trusted helper identity.

Do not recompute identity from mutable repository metadata during a run.
`workspace reuse` verifies the existing record without repairing it.

## Run and Revision State

The run manifest binds one prompt, one accepted revision, the lane generation,
and current public-safe status. Immutable snapshots remain under
`inputs/<revision>/prompt.md`.

Requirements refinement records stable clarification IDs, extracted
categories, compiled requirements digest, and one of:

```text
extracting | needs_clarification | ready
```

Material open questions prevent `ready`. Once ready, the workflow publishes a
complete Task Implementer-owned prompt-impact claim and immutable receipt.

## Prompt Impact State

Task Implementer owns:

```text
task-implementer/prompt-impact-claim-v1
task-implementer/prompt-impact-receipt-v1
task-implementer/prompt-impact-ledger-v1
task-implementer/prompt-impact-plan-v1
```

The claim classifies every extracted statement occurrence exactly once. The
receipt derives effects and `retain_plan` or `replan_required`; callers cannot
self-attest `no_effect`. Publication is serialized, append-only, and
compare-and-set. A conflicting orphan attempt is preserved and skipped.

The plan basis binds the exact current prompt-impact receipt, accepted
root-intent digest, canonical project-spec v2 receipt and bytes, and immutable
plan digest. Spec or prompt drift requires reconciliation or a distinct replan
before resource creation and dispatch.

`maintain-project-specs` provides the canonical parser, pair publisher, and
spec-validation receipt. Task Implementer stores that exact receipt as project
truth evidence, never as transition authority.

## Queue Contract

Only an explicit `run` may queue a different prompt while work is active.
Creating or saving a prompt does not queue it. Queue order is FIFO.

An edited queued prompt updates its accepted snapshot only after another
explicit `run`. Unaccepted queue-head drift blocks activation. After successful
finalization releases the active generation, activate the unchanged queue head
atomically. Never overtake blocked work.

## Steering Contract

Steering entries bind prompt ID, accepted revision, digest, snapshot, and a
sanitized summary. Classify each entry as pending, applied, blocked, or
no-effect. Apply steering only at a safe wave boundary. Material changes create
a new plan identity; locked plan bytes are never edited in place.

## Coordinator and Resume State

Coordinator-v7 is authoritative after creation. It indexes waves, immutable
tasks, plan digest, generation identity, current prompt-impact basis, and exact
Git/resource state.

`resume-control-v1` records one controlled transition:

- monotonic epoch;
- transition name;
- canonical argument object and digest;
- prior observed-state digest;
- phase and effect evidence;
- terminal digest.

Consume the returned resume token and arguments unchanged. Do not reconstruct
private commands from conversation or handoff text. A stale token fails before
effect; an interrupted observed effect completes without repeating mutation.

## Handoff Projection

`handoff.md` is the human-readable projection of accepted intent, tasks,
progress, evidence, and next action. It is planning input before coordinator
creation. Afterward, machine state is authoritative and handoff updates are
compare-and-set projections.

The handoff may summarize the canonical spec status but does not expose private
receipt paths or project-instruction decision fields. Immutable assignments
carry the machine-readable project-spec receipt. Existing applicable
instructions are read directly from the selected checkout.

## Project-Spec Boundary

Canonical specs are root-owned project truth. Their exact receipt participates
in plan and assignment evidence but never authorizes dispatch, cleanup,
finalization, or release.

- No lifecycle authorization command exists.
- No lifecycle contract-delta adoption command exists.
- No private lifecycle state path is accepted from a user or hook.
- No project-instruction receipt is copied into run state.
- Missing or invalid canonical specs require root reconciliation before a
  spec-dependent plan lock; private lifecycle state does not change workflow
  outcome.
- Historical lifecycle artifacts are ignored, not migrated.
- Workers inherit the root intent/receipt and return typed `spec_gaps`; they do
  not write specs.

## Finalization and Summary

Finalization requires all indexed waves done, no retained internal resources,
current workflow-owned prompt impact, clean exact lane head, and bounded final
`$align` evidence. It prepares and seals one deterministic `run-summary-v1`,
releases the generation, projects the handoff, and activates the next queue
head.

An interrupted finalization resumes from its own prepared summary and intent.
It never waits for a Stop hook or lifecycle seal. `ALREADY_COMPLETE` returns
the same sealed summary bytes.

## Public Status

`lane-report-v2` is a separate zero-write read model. It double-observes a
bounded evidence set and emits only a matching pair. It exposes progress,
totals, current/remaining steps, and one next public action—never prompt text,
internal IDs, branches, commits, state paths, raw diffs, or lifecycle data.

## Unsupported State

Coordinator v1 through v6, unfinished pre-interop runs, unsafe generated
workspace shapes, and ambiguous writer/resource state fail closed with
`WORKFLOW_UPGRADE_REQUIRED` or a precise recovery blocker. Do not add a legacy
compatibility reader or automatic migration.
