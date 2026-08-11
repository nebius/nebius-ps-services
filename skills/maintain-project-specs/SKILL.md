---
name: maintain-project-specs
description: Maintain canonical project requirements, design, and conditional project AGENTS.md lifecycle state for brownfield or greenfield work. Use when a selected project is missing docs/requirements.md or docs/design.md, when a user prompt changes project intent, when implementation changes require spec reconciliation, when former Task Implementer or Agentic SDLC spec ownership must be migrated, or when lifecycle hooks route contract planning or sealing here. This is the only semantic owner of project specs; Task Implementer and Agentic SDLC are adapters. Do not use for read-only questions after recording a bounded spec-impact waiver, or when the project has a committed disabled policy.
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

Keep `docs/requirements.md`, `docs/design.md`, and conditional project-root
`AGENTS.md` current without making hooks semantic document writers. Bind one
selected project, preserve human documentation outside canonical managed
regions, and issue one authoritative receipt before implementation and at
completion.

## When To Use

- The selected project is missing canonical requirements or design.
- A new user prompt changes accepted project intent.
- Implementation changed a documented boundary or invalidated design evidence.
- A former Task Implementer or Agentic SDLC spec pair needs one-time migration.
- The lifecycle hooks route planning, reconciliation, or sealing here.

## When Not To Use

- Do not use when the committed project policy is `mode: disabled`.
- Do not change specs for a genuinely read-only turn after recording the
  narrow matching waiver.
- Do not use hooks as semantic document authors.
- Do not create parallel requirements/design owners in Task Implementer,
  Agentic SDLC, or project-specific automation.

## Inputs

- Exact selected-project root and enclosing Git root.
- Current user intent and accepted steering relevant to that project.
- Existing canonical or legacy spec pair, repository documentation, focused
  source evidence, and tests.
- Optional committed `.codex/project-specs.json` policy.
- Current private lifecycle and project-instruction receipts when present.

## Required Reads

- Read [references/lifecycle.md](references/lifecycle.md) before changing files.
- Read [references/migration.md](references/migration.md) when either document
  has a former Task Implementer or Agentic SDLC owner.
- For missing specs, read the matching file under `assets/templates/` and
  replace every placeholder from repository evidence before changing a record
  from `draft` to a current status.
- Read every applicable repository instruction file and both canonical specs
  when present.
- Read only the repository documentation, source, and tests needed to establish
  or reconcile the current contract.

## Writes

- Canonical managed regions in `docs/requirements.md` and `docs/design.md`.
- Optional committed `.codex/project-specs.json` only when the project chooses
  an explicit policy.
- Private lifecycle state, receipts, exact rendered rules, locks, journals, and
  recoverable backups outside Git.
- Conditional project `AGENTS.md` only through `project-agent-instructions` at
  the terminal seal boundary.

## Invariants

- This skill exclusively owns the canonical spec schemas and validator.
- Hooks detect, gate, inject exact pending rules, record impact, and arbitrate
  completion. They never infer requirements or design decisions.
- Routine PostToolUse accounting is silent. It advances the write epoch after
  each successful material selected-project write, retries concurrent
  compare-and-swap races from current state, and invalidates a concurrently
  completed plan, armed seal, or seal instead of losing a late successful
  write. Canonical spec reconciliation admitted during planning or
  reconciliation is epoch-neutral. Stop owns the single accumulated
  reconciliation request.
- Bind one explicit selected-project root. Do not infer multiple project scopes
  from one write; split the work or request an exact scope.
- Preserve stable `REQ-*`, `FEAT-*`, `TI-REQ-*`, and `TI-DES-*` IDs. Never
  renumber existing records during migration or reconciliation.
- Preserve every byte outside canonical managed regions.
- Never persist raw prompts, transcripts, secrets, customer data, or repository
  file contents in lifecycle state. Persist only bounded identifiers, relative
  paths, versions, digests, and status.
- Do not write `AGENTS.override.md`.
- Render and inject any needed project rules before implementation. Apply the
  explicit project-instructions decision only after final spec reconciliation
  as the terminal seal mutation. A verified `not-needed` decision leaves a
  missing project `AGENTS.md` absent; only a needed transition creates or
  updates repository instructions. A fresh session is still required before
  changed repository instructions become loaded authority.
- Interpret user intent semantically. A clear statement that existing users
  depend on the project and future code or interface changes must not break
  them is explicit compatibility intent even without `GA`, `backward
  compatibility`, or another prescribed phrase. Capture it in requirements and
  design before implementation so the current rendered rules and future
  project instructions share one contract.
- Bind lifecycle-owned project-instructions inspect, render, plan, apply,
  verify, and seal inputs to the exact current-session private bundle. Deny a
  coordinator-shaped command when its action or evidence paths are
  malformed instead of letting it fall through as an ordinary Python read.
- Hooks are guardrails, not an absolute filesystem boundary. Proven material
  effects wholly outside the selected project pass through this lifecycle and
  remain subject to operating-system, Codex, destructive-action, and peer-hook
  policies. Reject mixed, dynamic, ambiguous, detached, or authoritative
  control-plane writes and audit unfinished state on the next session.

## Process

### 1. Bind scope and policy

Resolve the exact project root and Git root. Read applicable instructions and
the optional committed `.codex/project-specs.json`.

- Missing policy means `managed`.
- `mode: disabled` is the durable project opt-out.
- If the work is genuinely read-only, documentation-only with no contract
  impact, or outside the selected project, use the `waive` lifecycle command
  with the narrow matching reason.
- If one command would mutate more than one project scope, stop before mutation
  and split or clarify the scopes.

### 2. Inspect or migrate

Run:

```bash
python3 maintain-project-specs/scripts/project_specs.py inspect \
  --project-root <selected-project>
```

If the result is `SPEC_MIGRATION_REQUIRED`, run the paired migration once:

```bash
python3 maintain-project-specs/scripts/project_specs.py migrate \
  --project-root <selected-project>
```

Never hand-convert one file. If migration recovery artifacts exist, run
`recover` before retrying.

### 3. Establish or update requirements

For missing canonical documents, scan only the relevant portions of:

- repository and selected-project README files;
- existing architecture, ADR, API, operational, and user documentation;
- source entrypoints, public interfaces, configuration, and tests relevant to
  the requested behavior;
- the current user prompt and accepted steering.

Create the canonical requirements region. Record observable outcomes,
constraints, non-goals, acceptance criteria, and verification. For a new
prompt, update or append stable records and the change log. Mark replaced
requirements `superseded`; do not delete history.

When the user says real users depend on existing behavior and future changes
must remain safe, record compatibility as an accepted constraint. By default it
covers supported APIs and public import paths, CLI commands, flags, output and
exit behavior, configuration schemas and defaults, persisted formats, and
upgrade paths. Breaking a supported surface requires explicit approval, a
deprecation or migration plan, and regression coverage. Do not turn that
public contract into parallel internal paths; private internals retain one
canonical implementation.

### 4. Establish or update design

Map every applicable requirement to at least one current design record. Record
the selected approach, affected boundaries and interfaces, alternatives,
validation, rollout or rollback, and implementation evidence. On brownfield
projects, describe current behavior from code evidence before proposing a
change. Do not fabricate behavior when evidence is missing; record a bounded
open question.

Use `system-design-rules` for material architecture choices. Use `research`,
`app-stack`, or `ai-stack` only when their due-diligence boundary is actually
triggered.

### 5. Validate and prepare project instructions

Validate the tracked pair and atomically store the owner receipt under the
current `${CODEX_HOME}/project-specs` workflow directory:

```bash
python3 maintain-project-specs/scripts/project_specs.py validate \
  --project-root <selected-project> \
  --session-id <hook-session-id> \
  --output <private-workflow-dir>/spec-receipt.json
```

The coordinator constrains `--output` to the exact current-session
`spec-receipt.json` and writes mode `0600`; do not use shell redirection or
hand-author a receipt. Route it with one uncomposed canonical command; every
private path is absolute and belongs to the current lifecycle session:

```bash
python3 <canonical-project-agent-instructions-helper> inspect \
  --project-root <selected-project> \
  --spec-owner maintain-project-specs \
  --requirements docs/requirements.md \
  --design docs/design.md \
  --spec-receipt <private-workflow-dir>/spec-receipt.json \
  --runtime-config <private-workflow-dir>/runtime-config.json \
  --codex-home <codex-home> \
  --private-root <private-workflow-dir>/project-instructions \
  --output <private-workflow-dir>/project-instructions/manifest.json
```

The lifecycle hook requires the explicit active `--codex-home`; environment
fallback is not a valid coordinator binding. A relative output, another
session's bundle, a different interpreter, or a composed coordinator command
fails closed. The canonical helper is the installed
`project-agent-instructions` coordinator under the user skills root recognized
by the hook; do not substitute a checkout or basename-matching script.
Decide whether project rules are needed. A missing `AGENTS.md` is not itself
evidence that rules are needed. Record `not-needed` explicitly when no durable,
project-specific rule remains; its empty render is a successful decision and
does not create a file. Use `render` before implementation and pass the exact
rendered file, render state, and project-instructions private root to lifecycle
`plan`; the lifecycle reruns the authoritative renderer before injecting those
rules. For a managed lifecycle, these are the canonical current-session
`project-instructions/rules.md`, `project-instructions/render-state.json`, and
`project-instructions/` paths; alternate same-project bundles are invalid. Do
not run `apply` yet.

Explicit compatibility intent is durable and project-specific. Choose `needed`
with the default compatibility rules unless active same-directory project
instructions already provide an equivalent contract, in which case choose
`existing-sufficient`. Personal global instructions are conflict context only:
do not copy them and do not let a global no-compatibility default suppress the
project rule. If a same-directory override is active and lacks a necessary
rule, return the fail-closed instructions-gap blocker instead of creating a
dormant file behind it.

The canonical pair must be tracked before validation. For a newly created
file, use only the hook-admitted intent-only transition, bound to the selected
project and one or both exact spec paths:

```bash
git -C <selected-project> add -N -- docs/requirements.md docs/design.md
```

This records no file contents and does not advance the lifecycle write epoch.
General staging and arbitrary index mutation remain outside this exception.
If a caller-authored runtime declaration or decision input has a broader mode,
the hook similarly admits only an uncomposed numeric mode-`0600` transition for
one exact current-session input.

### 6. Open implementation

Run lifecycle `plan`, then `open`, with the current hook-provided session ID and
opaque `--turn-token`. The hook injects both values and verifies them before
allowing a coordinator transition; agents do not need the raw turn ID. The
transition is:

`planned -> implementation-open -> reconciliation-required -> planned -> seal-armed -> sealed`.

Multiple declared-scope edits remain allowed after the first write. Direct
spec and project-instruction edits remain coordinator-owned.
A successful material tool admitted before a concurrent plan or terminal
transition may report completion afterward. Its PostToolUse accounting returns
the lifecycle to `reconciliation-required`, advances the epoch, and invalidates
the later evidence; reconcile and plan again rather than bypassing that state.

### 7. Reconcile and seal

At the terminal Stop boundary, review the accumulated selected-project Git
status, staged and unstaged diff, untracked implementation files, current task
context, and accepted user intent once. Classify the final delta as follows:

- update requirements only for accepted observable behavior, project intent,
  constraints, non-goals, or acceptance-criteria changes;
- update design for component boundaries, interfaces, data or control flow,
  failure handling, security, operations, rollout, or meaningful implemented
  evidence changes;
- update both when an accepted intent change also changes its implementation
  design;
- leave both documents byte-identical for refactors, formatting, test-only
  edits, reverted changes, or bug fixes that restore the existing documented
  contract without changing design;
- when impact remains ambiguous, inspect further or request a decision and
  leave the lifecycle unsealed.

Hooks record only the bounded write epoch and never make this semantic
classification or persist a per-edit content journal. Even when neither spec
changes, validate again at the latest write epoch, rerender the conditional
project instruction decision, and then:

1. run `project-agent-instructions apply` exactly once to materialize the
   final decision state; missing plus `not-needed` writes only verified private
   state, while repository `AGENTS.md` changes only for creation, attachment,
   refresh, adoption, or retirement outcomes;
2. independently run `project-agent-instructions verify`;
3. run lifecycle `seal` with the verified state and its private root; sealing
   accepts and records the exact `reload_required` result only when the specs
   still match the final planned receipt and write epoch;
4. report the exact project-instructions outcome and whether a file was
   created, changed, left absent, or already sufficient; require a fresh
   session only when project instruction bytes changed.

Do not perform repository writes after sealing. A new user prompt starts a new
lifecycle.

## Task Implementer and Agentic SDLC

- Task Implementer may refine intent and author `TI-REQ-*` / `TI-DES-*`
  records, but it invokes this validator and emits this owner receipt.
- Agentic SDLC requirement and design phases may author rich `REQ-*` /
  `FEAT-*` records, but their templates use the canonical managed schemas and
  validator.
- Neither workflow may emit an independently authoritative legacy receipt.
- Existing legacy pairs must migrate before either workflow proceeds.

## Verification

Run the focused owner, hook, adapter, and project-instruction tests. Then use
`$code-review` for changed-scope correctness, `$linter` for Python/Markdown/
Shell surfaces, `$apply-security` for hook, path, receipt, and private-state
controls, and `$align` for cross-skill docs, tests, installer, and changelog
alignment.

Source validation does not prove installation or live activation. Installation,
installed parity, Codex restart, hook trust, and a fresh-session behavior test
are separate gates.

## Idempotency

- Repeated inspection and validation of unchanged tracked specs return the same
  receipt and do not write repository files.
- Repeated lifecycle events preserve the same phase unless an authorized
  transition or relevant digest changes.
- Migration is one-shot and returns unchanged for a canonical pair; it rejects
  mixed-owner or partially migrated pairs instead of creating a second
  representation.
- Project-instruction rendering is deterministic; unchanged owned regions and
  human prefixes remain byte-identical.

## Failure Handling

- Stop on ambiguous project scope, mixed spec ownership, stale receipts,
  malformed policy, unsafe paths, mixed project/external effects, dynamic or
  detached writers, authoritative lifecycle-owned project-specs targets, or
  concurrent mutation. Do not treat config, hooks, task state, installed
  skills, credentials, or other fixed external user files as this selected
  project's control plane; pass them to their actual policy owners.
- Preserve migration journals, locks, and backups for explicit `recover` after
  an interrupted paired transition.
- Leave lifecycle state unsealed when requirements, design, project rules, or
  implementation evidence still need reconciliation.
- Report hook coverage limits honestly; do not treat a hook allow decision as
  proof that no other filesystem writer exists.

## Must Not

- Do not store prompts, transcripts, secrets, customer data, repository file
  contents, private endpoints, or absolute private-state paths in public docs
  or lifecycle state.
- Do not create `AGENTS.override.md`, duplicate spec files, compatibility
  shims, or an independent coordinator receipt.
- Do not renumber stable records, silently migrate only one document, bypass a
  stale receipt, or seal before final reconciliation.
- Do not install, register, trust, or activate hooks as part of ordinary source
  validation.

## Completion Criteria

- The exact selected project has a canonical, tracked, current requirements and
  design pair or a committed disabled policy.
- Every active requirement has current design coverage and the shared validator
  emits an exact Git-bound v3 receipt.
- Any necessary project rules were rendered before implementation and applied
  and verified only at terminal sealing.
- Lifecycle state is sealed with no later repository write, or the precise
  blocker and recovery action are reported.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

Prioritize schema or hook edge cases, migration failures, validation gaps, and
false-block patterns. Keep project facts in project docs and transient task
facts in private task state.

## Output Contract

Return the selected project and policy, lifecycle phase, canonical spec paths
and receipt digest, requirement/design coverage status, project-instruction
render/apply outcome, explicit `AGENTS.md` file effect, validation performed,
reload requirement, and any exact blocker or recovery action. For
`not-needed`, say that no file was created or changed and that a missing target
remains absent. Do not print raw prompts or private state.
