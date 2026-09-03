---
name: maintain-project-specs
description: "Maintain canonical requirements and design when direct root-user intent or proven delivery changes project truth, across ordinary sessions, Task Implementer, and Agentic SDLC. Hooks never gate tools, Stop, or workflow completion."
---

# Maintain Project Specs

## Help

For `$maintain-project-specs --help` or `$maintain-project-specs -h`, return
concise help and stop before any workflow step. State the purpose and
invocation policy. Show exact usage for every public action. Describe each
public action, positional argument, and flag in one concise line, including
`-h, --help`; say "No additional public flags" when there are no others. Use
only the documented public interface. For internal or coordinator-only skills,
state that boundary and that no standalone public workflow action exists.
After the selected `SKILL.md` is loaded, help is report-only: do not call any
additional tools, inspect project state, or modify files, private state, Git,
or external systems. Never expose private helper actions or flags or treat
help as workflow authorization.

## Purpose

Keep canonical `docs/requirements.md` and `docs/design.md` useful and current
for ordinary root-agent sessions, Task Implementer, and Agentic SDLC. The root
agent classifies every direct user prompt at statement level. Durable product
intent is reconciled into requirements and a ready design before
implementation; implementation and verification evidence are reconciled after
the work. Non-lifecycle requests leave both documents unchanged.

This skill is the only semantic owner of project-spec schemas, templates,
stable IDs, traceability, paired publication, and strict validation. Its hooks
observe document state and stage metadata-only intake; the active root agent,
not a hook, performs semantic classification and repository writes.

## Advisory Authority Boundary

- Spec status never denies a tool, injects a required continuation,
  returns `continue:false`, blocks Stop, or prevents session completion.
- Missing, stale, malformed, contended, locked, unsafe, or unavailable private
  lifecycle state produces at most a bounded advisory message.
- Task Implementer and Agentic SDLC own their prompt-impact, plan, execution,
  cleanup, finalization, and recovery gates. Their root coordinators use this
  owner contract for canonical spec parsing, paired publication, and receipts;
  those receipts describe project truth but never authorize workflow actions.
- Project-instruction creation or mutation is a separate explicit workflow. It
  is never automatically rendered, applied, verified, reloaded, or sealed by
  this skill or its hooks.
- Existing applicable `AGENTS.md` files remain authoritative instructions for
  the current session. This skill never weakens them and never writes
  `AGENTS.override.md`.
- Hooks are intake and observation guardrails only, never project
  authorization or repository writers.
- Personal global instructions are conflict context only and are never copied
  into committed project files.
- Historical lifecycle bundles may remain readable for diagnostics and bounded
  retention. They are not migrated into active authority.

## When To Use

- Requirements or design are missing or visibly stale.
- Accepted user intent or implementation changes the documented contract.
- A direct root-user prompt contains durable product behavior, constraints,
  non-goals, compatibility promises, architecture, interfaces, data ownership,
  security boundaries, rollout, or acceptance changes.
- A completed implementation supplies evidence needed to advance design
  delivery state or satisfy a requirement.
- A user explicitly requests reconciliation, validation, or legacy migration.
- A hook advisory says spec review may be useful.

## When Not To Use

- Do not mutate specs for explanations, brainstorming, status checks,
  read-only/report-only requests, execution control, one-off operational work,
  formatting-only edits, or implementation that restores already-documented
  behavior without changing the contract.
- Do not infer that an advisory hook message authorizes repository changes.
- Do not create a parallel prompt-impact or plan owner inside Task Implementer
  or Agentic SDLC.
- Honor committed `mode: disabled` policy by skipping lifecycle observation and
  automatic spec maintenance.

## Inputs

- Exact selected-project and enclosing Git roots.
- Current canonical requirements and design when present.
- The complete direct root-user prompt, classified statement by statement,
  plus focused implementation, test, and documentation evidence needed to
  classify contract drift. Never treat generated continuation text or worker
  prompts as new user intent.
- Optional committed `.codex/project-specs.json` policy and passive historical
  private evidence when inspection or retention needs it.

## Required Reads

- Read `references/lifecycle.md` for the advisory hook and private-state model.
- Read `references/migration.md` before changing legacy managed regions.
- Read applicable repository instructions and both canonical specs when present.
- Read only the repository docs, source, tests, and accepted intent needed to
  establish the current contract.
- For missing specs, use the matching template under `assets/templates/` and
  replace every placeholder from evidence.

## Writes

- Canonical managed regions in `docs/requirements.md` and `docs/design.md` only
  when accepted durable intent or proven delivery evidence changes project
  truth. Publish the pair through the owner transaction even if only one
  managed region changes.
- Owner-only intake metadata, journals, and validation receipts under the
  exact private session root. Intake stores digests and classification state,
  never raw prompt text.
- Paired migration journals and recoverable backups only for explicit legacy
  migration or recovery.
- Never project instructions, lifecycle phases, workflow state, or
  `AGENTS.override.md`.

## Public Helper Surface

`scripts/project_specs.py` exposes:

```text
inspect  --project-root <path>
validate --project-root <path> [--output <private-path>]
publish  --project-root <path> --requirements-candidate <path> \
  --design-candidate <path> --expected-head <sha> \
  --expected-requirements-sha256 <sha-or-absent> \
  --expected-design-sha256 <sha-or-absent> --operation-id <id>
migrate  --project-root <path>
recover  --project-root <path>
```

- `inspect` and `validate` are advisory and return structured observations.
  Validation findings do not create lifecycle authority.
- `publish` is the only normal repository-write path. It validates the complete
  candidate pair and compare-and-set checks Git plus both predecessor digests.
- `migrate` and `recover` are explicit repository-mutating operations and keep
  their existing safety, ownership, and rollback checks.
- `start-prompt`, `plan`, `open`, `seal`, and `waive` are retired. Do not add
  no-op compatibility aliases.

## Canonical Spec Contract

- Schema v2 requirements use `draft`, `active`, `blocked`, `satisfied`, or
  `superseded`; designs use readiness `draft`, `ready`, `blocked`, `stale`, or
  `superseded` independently from delivery `unassessed`, `not-started`,
  `implemented`, or `verified`.
- Preserve stable `REQ-*`, `FEAT-*`, `TI-REQ-*`, and `TI-DES-*` IDs. New
  records use canonical `REQ-*` and `FEAT-*`; legacy `TI-*` IDs remain stable
  only after explicit migration.
- Update accepted records in place; append genuinely new IDs; mark obsolete
  records `superseded` instead of silently deleting truth.
- Every active or satisfied requirement maps to at least one current design
  record. Each design record's marker mapping and body mapping agree.
- Preserve every byte outside canonical managed regions.
- Keep requirements focused on durable product truth and acceptance methods.
- Keep design focused on selected behavior, boundaries, state, alternatives,
  validation, evaluation, rollout, and separate implementation and
  verification evidence. A satisfied requirement requires verified delivery.
- Never store prompts, transcripts, secrets, customer data, private endpoints,
  repository file contents, or raw logs in lifecycle state or canonical specs.
- Interpret compatibility intent semantically even without `GA`, `backward
  compatibility`, or another prescribed phrase; record explicit supported-user
  promises in requirements and design before changing the contract.

## Process

1. Resolve the exact selected project and Git root. Explicit scope supplied by
   the current user wins; otherwise use the startup cwd. If multiple plausible
   projects remain, ask before any spec write. Read applicable instructions
   and optional `.codex/project-specs.json` policy.
2. Inspect existing documents and ownership markers. If legacy ownership is
   detected, stop ordinary reconciliation and use the explicit migration
   contract.
3. Classify each statement in the direct root-user prompt as durable
   requirement intent or non-lifecycle. Redact sensitive content from all
   private/public lifecycle artifacts; if safe classification cannot be
   persisted, continue the user task and report the limitation.
4. Before implementation, reconcile durable requirements and a covering ready
   design. A ready design may legitimately have delivery `not-started`.
5. Publish the complete pair with compare-and-set checks over Git `HEAD` and
   both prior file digests. Preserve human-owned prefixes and suffixes exactly;
   roll back a split write from the private journal.
6. After implementation and focused verification, reconcile the same records
   with implementation and verification evidence. Advance a requirement to
   `satisfied` only when its covering delivery is `verified`.
7. Validate schema, stable IDs, independent readiness/delivery statuses,
   marker/body agreement, total traceability, and bounded content. Report
   current or pending findings plus exact files changed and focused proof.
8. If an explicit project-instruction mutation is separately requested, hand
   off to `project-agent-instructions`; it owns its own decision, provenance,
   conflict, apply, verify, and reload safety.

## Hook Contract

- `SessionStart` reads the actual canonical document markers through bounded,
  no-follow, owner-controlled regular-file reads. It is silent for a current
  v2 pair and may emit one bounded `CONTRACT_PENDING`, `CONTRACT_INVALID`, or
  `CONTRACT_MIGRATION_REQUIRED` observation. It never derives project status
  from a missing private `lifecycle.json`.
- `UserPromptSubmit` stages only bounded owner-private intent metadata and
  injects root-agent classification guidance. It skips subagents, generated
  continuations, system/compaction turns, Stop turns, and secret-bearing input.
- Project-spec `PreToolUse`, `PostToolUse`, and `Stop` registrations do not
  exist. The project-spec delegate is absent from shared Stop arbiters.
- Peer delegates such as Agentic SDLC or troubleshooting retain their own
  authority.
- Hook exceptions and unsafe/private-state failures degrade to
  neutral/advisory behavior; they never fail closed on project lifecycle
  grounds.
- Hook source changes do not prove activation. Verify source, installed parity
  in a disposable Codex home, fresh-process loading, and neutral hook behavior
  separately.

## Private Retention

Retention is housekeeping, not authority:

- Count current-session allocated bytes in aggregate pressure calculations but
  never delete the current session.
- Retain valid terminal state for 30 days and abandoned resumable state for 90
  days.
- Use 1 GiB/768 MiB allocated-storage hysteresis only with complete trustworthy
  aggregate and registry evidence.
- Incomplete, malformed, digest-mismatched, foreign, pending-publication, or
  unsafe evidence disables pressure deletion.
- Preserve stable workspace/session lock ordering, owner-only modes,
  same-device rename journals, directory-relative no-follow deletion, and
  exact current-session protection.
- A foreign project scope is not pending evidence for the selected workspace.

## Idempotency

- Repeated inspection and validation of unchanged tracked specs return the same
  semantic receipt and do not modify repository files.
- Reconciliation preserves stable IDs and human-owned bytes; rerunning it on
  current documents is byte-identical.
- Replaying one publication operation returns the same receipt when both
  candidates are already present and completes only a digest-proven
  before/after split left by interruption; unknown bytes fail closed.
- Publication retains no-follow project/docs directory descriptors, rechecks
  Git and both document byte states at every replacement boundary, and rolls
  back only candidate bytes that remain unchanged. Canonical inspection takes
  the matching shared lock so it cannot observe an in-process split pair.
- Migration and recovery replay only their exact journaled transition and never
  create a second owner representation.
- Retention retries reclassify exact restored evidence and never treat a prior
  failed or ambiguous classification as deletion authority.

## Must Not

- Do not expose, recreate, or alias retired lifecycle transition commands or
  importable transition functions.
- Do not make receipts, private state, project instructions, or helper
  availability authorize ordinary work or workflow completion.
- Do not store prompts, transcripts, secrets, private endpoints, customer data,
  repository contents, raw logs, or absolute private paths in reusable output.
- Do not renumber stable records, hand-convert one legacy document, bypass
  migration journals, or mutate project instructions from this skill.

## Failure Handling

- Validation or inspection failure returns a sanitized advisory with the exact
  code and next manual action.
- Explicit migration/recovery failure remains blocked and preserves backups or
  journals for safe retry.
- Ambiguous project scope, unsafe managed markers, mixed ownership, or
  irreconcilable active records stop repository mutation but never stop the
  agent session.
- Do not bypass repository instructions, security policy, destructive-action
  rules, or another workflow's own gates.

## Completion Criteria

- Every direct root-user statement was classified, and canonical
  requirements/design accurately reflect durable intent plus proven delivery
  evidence, or the exact advisory limitation is reported without blocking the
  session.
- Stable IDs, ownership markers, traceability, and managed-region preservation
  validate.
- No automatic project-instruction mutation or reload occurred.
- Hook surfaces remain nonblocking, the manifest contains only `SessionStart`
  and `UserPromptSubmit`, and the project-spec Stop delegate remains absent.
- Focused tests cover advisory hooks, retention accounting, migration safety,
  and workflow independence.

## Output Contract

Return scope, observed spec status, changed files, validation performed,
advisory findings, and any explicit migration blocker. Never claim that a
lifecycle phase authorizes or prevents further work.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## References

- `references/lifecycle.md`
- `references/migration.md`
- `assets/templates/requirements.md.template`
- `assets/templates/design.md.template`
