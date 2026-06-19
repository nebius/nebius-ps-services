# Agentic SDLC Design

## Table Of Contents

- [Purpose](#purpose)
- [Core Concepts](#core-concepts)
  - [Agent model](#agent-model)
  - [Agent-selected phases](#agent-selected-phases)
  - [Committed product truth](#committed-product-truth)
  - [Private local run state](#private-local-run-state)
  - [Hooks as guardrails](#hooks-as-guardrails)
  - [MCP servers and external capabilities](#mcp-servers-and-external-capabilities)
- [Requirements And Design Templates](#requirements-and-design-templates)
- [Workflow](#workflow)
- [Workflow Verification](#workflow-verification)
  - [Verification principles](#verification-principles)
  - [Quick preflight test](#quick-preflight-test)
  - [Full workflow test](#full-workflow-test)
  - [Report interpretation](#report-interpretation)
  - [Fix and rerun policy](#fix-and-rerun-policy)
- [Skill Responsibilities](#skill-responsibilities)
  - [`sdlc-create-requirements`](#sdlc-create-requirements)
  - [`sdlc-start`](#sdlc-start)
  - [`sdlc-gather-context`](#sdlc-gather-context)
  - [`sdlc-create-design`](#sdlc-create-design)
  - [`sdlc-create-plan`](#sdlc-create-plan)
  - [`sdlc-tdd`](#sdlc-tdd)
  - [`sdlc-implement-plan`](#sdlc-implement-plan)
  - [`sdlc-validate-codes`](#sdlc-validate-codes)
  - [`sdlc-unit-tests`](#sdlc-unit-tests)
  - [`sdlc-evaluate`](#sdlc-evaluate)
  - [`sdlc-gui-test`](#sdlc-gui-test)
  - [`sdlc-tui-test`](#sdlc-tui-test)
  - [`sdlc-classify-failure`](#sdlc-classify-failure)
  - [`sdlc-align-specs`](#sdlc-align-specs)
  - [`sdlc-commit`](#sdlc-commit)
  - [`sdlc-uat-tests`](#sdlc-uat-tests)
  - [`create-pr`](#create-pr)
  - [`review-pr`](#review-pr)
  - [`sdlc-merge-pr`](#sdlc-merge-pr)
- [Failure And Retry Model](#failure-and-retry-model)
- [Guarded Git Actions](#guarded-git-actions)
- [Resume And Idempotency](#resume-and-idempotency)
- [Boundaries](#boundaries)

## Purpose

The Agentic SDLC workflow is a skill-driven state machine for turning a user
prompt into committed implementation, product-level UAT, pull request creation,
PR review, and an explicitly requested merge.

There is no workflow CLI. The agent chooses the next SDLC skill, committed
project documents hold product truth, private local state records execution,
MCP servers provide external capabilities when needed, and optional hooks
enforce only non-negotiable runtime guardrails.

## Core Concepts

### Agent model

The workflow uses the Codex agent as the runtime decision maker. Skills are
phase contracts, instructions, templates, and validation rules that the agent
loads at the right point in the lifecycle; they are not long-running services.

The parent agent owns final decisions, file edits, state updates, validation,
and the final report. Optional bounded helper agents can be used for
read-heavy side work when a separate context-management policy authorizes
delegation, but helpers do not own SDLC phase selection or committed output.
MCP servers are tools the agent can call through the selected phase skill.

### Agent-selected phases

The SDLC is advanced by Codex choosing skills, not by a standalone command
runner. `sdlc-start` is the coordinator skill. It reads the committed specs and
private local run state, selects one current feature, and returns exactly one
next recommended skill.

Every phase skill owns a narrow responsibility. For example,
`sdlc-create-requirements` owns `docs/requirements.md`, `sdlc-create-design`
owns `docs/design.md`, `sdlc-create-plan` owns a locked private plan, and
`sdlc-commit` owns a local feature-scoped commit after evidence passes.

### Committed product truth

The committed SDLC truth inside a target project is:

- `<project>/docs/requirements.md`
- `<project>/docs/design.md`

These files are intentionally committed because they describe what the product
must do and how features are designed. Other SDLC artifacts are local runtime
state and must not be committed.

Ownership is strict:

- Only `sdlc-create-requirements` writes `docs/requirements.md`.
- Only `sdlc-create-design` writes `docs/design.md`.
- Other skills route spec changes back to the owning skill instead of editing
  those files directly.

### Private local run state

Execution state lives under:

```text
~/.codex/sdlc-runs/<project-id>/<run-id>/
```

The project-level SDLC state also has an `active-run.json` pointer and an
`active.lock` lock file under `~/.codex/sdlc-runs/<project-id>/`. The active
run directory stores the active feature, run metadata, checkpoints, feature
queue, fingerprints, steering, context packs, locked plans, evidence,
screenshots, transcripts, failure logs, history, and short-lived authorization
files. It is the recovery source when conversation context is lost.

Important files include:

- `run.json`: active run metadata and run-level status.
- `current-state.json`: current feature, phase, status, retry counts, and next
  recommended skill.
- `feature-queue.json`: ordered feature queue derived from `docs/design.md`.
- `fingerprints.json`: requirement, design, context, and plan fingerprints.
- `checkpoints/checkpoint-*.json`: append-only state snapshots.
- `checkpoints/latest.json`: pointer to the newest complete checkpoint.
- `STEERING.md`: temporary runtime steering, not a second requirements file.
- `context/FEAT-*.context.md`: compact context packs for design and planning.
- `plans/FEAT-*.plan.vN.md` and `.lock`: private locked feature plans.
- `evidence/`: validation, test, evaluation, UAT, PR, review, merge, and
  commit evidence.
- `history/iteration-*.md`: append-only state-transition history.
- `permissions/`: short-lived authorization files for guarded commit, PR, and
  merge actions.

`sdlc-start` resumes from the latest checkpoint and current state. It does not
depend on conversation memory to know the current phase, feature, retry count,
or next skill.

### Hooks as guardrails

The optional Agentic SDLC hook source lives in:

```text
sdlc-start/assets/hooks/
```

The bundle contains:

- `pre_tool_use_sdlc_policy.py`
- `stop_sdlc_continue.py`
- `lib/sdlc_policy.py`
- `lib/sdlc_state.py`
- `tests/test_sdlc_hooks.py`

Hooks are guardrails, not the workflow engine. Phase selection remains owned by
`sdlc-start`.

The Stop hook can route an active run back through `sdlc-start` with a
continuation prompt. It stops instead of continuing when the run is complete,
paused, blocked, waiting on human input, over the iteration or retry budget, or
making no progress after repeated continuation attempts. It can continue to
`sdlc-start` when local state recommends another phase, steering needs to be
persisted, all features are committed and UAT still needs to run, or UAT failed
with an addressable classification. It never auto-continues into
`sdlc-merge-pr`; merge requires an explicit user request.

The PreToolUse hook can deny unsafe actions such as out-of-scope writes,
credential writes, secret-bearing shell or patch content, locked-plan edits,
private SDLC state leaks into the repository, destructive Git commands,
protected-branch commit or push attempts, force pushes, branch deletion, and
guarded Git or GitHub actions without valid short-lived authorization. It also
allows writes under `$CODEX_HOME/task-state` so global task-state checkpoints
remain separate from private SDLC run state.

The hook bundle is source code in the skills repository. Installed copies under
`$CODEX_HOME/hooks` are runtime artifacts and can drift. Hook fixes should be
made in `sdlc-start/assets/hooks/`, validated locally, and then synced
deliberately with the installer.

### MCP servers and external capabilities

MCP servers are capability providers, not the state machine. Phase skills use
them when the feature requires external context or interaction:

- official docs and package documentation for version-sensitive behavior
- Browser or Playwright for GUI evaluation
- GitHub for PR creation, PR review, checks, and merge readiness
- Slack, Confluence, Jira, or internal knowledge sources for approved context
- other domain MCP servers when a feature needs that external system

The selected SDLC skill remains responsible for deciding what evidence is
needed, how to use the MCP result, and where to store the summary. Raw logs,
secrets, private tokens, and broad copied source material do not belong in
committed docs or durable state. When the optional hooks are installed, GitHub
PR or merge MCP writes require the matching short-lived authorization, while
other external write MCP calls are gated by active SDLC state plus secret and
write-target checks.

## Requirements And Design Templates

The two committed product-truth documents are created from templates in their
owning skills:

- `sdlc-create-requirements/assets/templates/requirements.md.template`
- `sdlc-create-design/assets/templates/design.md.template`

`sdlc-create-requirements` uses the requirements template when
`<project>/docs/requirements.md` does not exist. The template defines the
front matter, source prompt, problem, desired outcome, users, success
definition, constraints, environment, external systems, stable `REQ-*` blocks,
acceptance criteria, negative criteria, validation method, test method,
evaluation method, open questions, decision log, and change log.

The requirements template also records:

- `created_by_skill: "sdlc-create-requirements"`
- `updated_by_skill: "sdlc-create-requirements"`
- a user edit policy that routes updates through `sdlc-create-requirements`
- an ID policy that forbids renumbering existing requirement IDs

`sdlc-create-design` uses the design template when
`<project>/docs/design.md` does not exist. The template defines the front
matter, source requirements link, design summary, technology choices,
architecture sections, stable `FEAT-*` feature blocks, requirements covered,
context evidence, implementation boundaries, TDD success criteria, validation
plan, test plan, evaluation plan, done definition, UAT strategy, risks, open
design questions, decision log, and change log.

The design template also records:

- `created_by_skill: "sdlc-create-design"`
- `updated_by_skill: "sdlc-create-design"`
- `source_requirements: "docs/requirements.md"`
- a user edit policy that routes updates through `sdlc-create-design`
- an execution plan policy stating that plans are local runtime artifacts and
  must not be committed
- an ID policy that forbids renumbering existing feature IDs

## Workflow

The high-level workflow is:

```text
User prompt
  |
  v
sdlc-create-requirements
  |
  v
<project>/docs/requirements.md
  |
  v
sdlc-start
  |
  v
sdlc-gather-context
  |
  v
sdlc-create-design
  |
  v
<project>/docs/design.md
  |
  v
Feature loop
  |
  v
sdlc-create-plan, local and locked
  |
  v
sdlc-tdd
  |
  v
sdlc-implement-plan
  |
  v
sdlc-validate-codes
  |
  v
sdlc-unit-tests
  |
  v
sdlc-evaluate
  |
  v
sdlc-classify-failure, if a phase fails
  |
  v
fix loop to the earliest responsible phase, if needed
  |
  v
sdlc-align-specs
  |
  v
sdlc-commit
  |
  v
next feature
  |
  v
sdlc-uat-tests
  |
  v
create-pr
  |
  v
review-pr
  |
  v
sdlc-merge-pr, only if explicitly requested
```

The implementation state schema uses this phase order:

1. requirements
2. context
3. design
4. plan
5. sdlc-tdd
6. implementation
7. validation
8. test
9. evaluation
10. sdlc-align-specs
11. sdlc-commit
12. uat
13. create-pr
14. review-pr
15. sdlc-merge-pr, only after explicit user request

`sdlc-classify-failure` is the routing mechanism for failed phases rather than
a normal happy-path phase. It records the failure class and sends the loop back
to the earliest responsible phase, or stops for human input, policy, or
environment blockers.

## Workflow Verification

`agentic-sdlc-test` is the external verification skill for this workflow. It is
not an SDLC phase and does not use the `sdlc-` prefix. Use it to verify the
workflow against this design, inspect global `sdlc-*` skill discovery, check
hook configuration, run disposable PreToolUse and Stop hook fixture tests, and
write a verification report to `~/.codex/sdlc-verification/report.md`.

### Verification principles

The design document is the workflow contract. The verifier must compare the
installed SDLC skills, hook configuration, disposable run state, and report
evidence back to this document instead of relying on conversation memory or
stale prior reports.

The verifier must remain safe and idempotent:

- use a disposable verification project only
- keep private verification state out of committed repositories
- avoid changing installed skills, hooks, hook trust, or agent configuration
- avoid pushing, opening real PRs, merging, publishing, or touching production
  resources
- route the disposable golden-path run through the normal SDLC phase skills
  instead of creating a workflow CLI

The verifier writes only verification-owned state under:

```text
~/.codex/sdlc-verification/
```

### Quick preflight test

Use the quick preflight when the SDLC skills, hook source, hook configuration,
or this design document changed and you need a safe readiness check:

```text
$agentic-sdlc-test Verify the Agentic SDLC workflow against docs/agentic-sdlc-design.md and write a safe report.
```

The skill runs the deterministic helper from the skills repository:

```bash
python3 agentic-sdlc-test/scripts/verify_agentic_sdlc.py
```

The preflight must verify and record:

- global `sdlc-*` skill folders and `SKILL.md` front matter
- duplicate SDLC skill-name detection
- configured PreToolUse and Stop hooks
- preservation of non-SDLC hook boundaries such as `SessionStart` and
  `UserPromptSubmit`
- PreToolUse allow and deny fixture cases
- Stop terminal cases: no active run, complete, paused, blocked, human input,
  max iteration, retry budget, no progress, and merge without explicit request
- Stop continuation cases through `sdlc-start`
- disposable-project creation and private-state staging checks
- report generation at `~/.codex/sdlc-verification/report.md`

A successful quick preflight may still return `PARTIAL` when the full
agent-driven golden path has not been completed. That status is expected until
the disposable workflow run, idempotency run, change-request run, failure-loop
run, and steering checks have evidence.

### Full workflow test

Use the full workflow test before treating the Agentic SDLC system as validated
end to end. It must run only in the disposable verification project created
under `~/.codex/sdlc-verification/disposable-project/`.

The golden-path feature is intentionally small:

```text
Create a Python validator for a Nebius-style resource name:
- lowercase letters, numbers, and hyphens only
- starts with a letter
- 3 to 32 characters
- structured validation errors
- tests and evaluation evidence
```

Run the normal SDLC phase skills through the disposable project. Do not add a
new workflow CLI and do not let hooks orchestrate phases.

Required happy-path evidence:

- `sdlc-create-requirements` creates committed requirements with stable
  `REQ-*` IDs.
- `sdlc-start` creates or resumes private local run state and recommends one
  next skill.
- `sdlc-gather-context` records a compact context pack.
- `sdlc-create-design` creates committed design with stable `FEAT-*` IDs.
- `sdlc-create-plan` writes a private locked plan version.
- `sdlc-tdd` records test intent before implementation.
- `sdlc-implement-plan` changes only the disposable project for the current
  feature.
- `sdlc-validate-codes`, `sdlc-unit-tests`, and `sdlc-evaluate` record passing
  evidence or route failures for classification.
- `sdlc-align-specs` confirms requirements, design, plan, implementation, and
  evidence agree.
- `sdlc-commit` creates one local feature-scoped commit only after evidence
  passes.
- `sdlc-uat-tests` records product-level UAT evidence after all features are
  committed.
- private SDLC state, plans, screenshots, transcripts, and evidence stay out
  of the disposable project Git index.

After the happy path, verify these recovery behaviors:

- rerun with no product change and confirm no duplicate requirements, design
  features, plans, tests, commits, or evidence
- apply the change request `Allow underscores when explicitly configured` and
  confirm stable IDs, a new plan version only when needed, scoped code and test
  changes, refreshed evidence, and a new local feature commit
- inject one controlled validation, test, bad-test, design, spec-gap, or
  environment failure at a time, then confirm `sdlc-classify-failure` routes to
  the earliest responsible phase before retry
- add `Pause after the current feature. Do not create a PR.` to `STEERING.md`
  and confirm `sdlc-start` and Stop continuation honor it
- confirm clearing steering allows resume
- run optional GUI or TUI smoke checks only against harmless local disposable
  targets when the required harness is available

The full workflow test must not call `create-pr`, `review-pr`, or
`sdlc-merge-pr` against a real remote. Merge remains outside verification
unless the user explicitly requests a separate merge exercise.

### Report interpretation

The report status means:

- `PASS`: required static, hook, golden-path, idempotency, change-request,
  failure-loop, steering, and continuation checks passed; optional GUI or TUI
  smoke checks may be `NOT APPLICABLE`.
- `PARTIAL`: the automated preflight passed, or a non-critical optional check
  is unavailable, but one or more full workflow checks still need disposable
  evidence.
- `FAIL`: safety hooks, Stop continuation, state persistence, private-state
  protection, or the disposable golden-path workflow failed.

`PARTIAL` is not a production-readiness result. It means the workflow is safe
enough to continue disposable verification, not that it is proven for real
project use.

### Fix and rerun policy

When verification finds a gap, fix the source of truth at the narrowest
responsible layer:

- update this design document when the contract is unclear, incomplete, or
  contradictory
- update the relevant `sdlc-*` skill when the phase contract is wrong
- update `sdlc-start/assets/hooks/` when the guardrail source is wrong
- sync installed hooks only through the approved hook installer flow after
  source fixes are reviewed
- rerun `$agentic-sdlc-test` after every fix

Do not update the report to hide a real failure. The report is evidence, not
the contract.

## Skill Responsibilities

### `sdlc-create-requirements`

Converts user intent, tickets, issues, Slack threads, Confluence pages, GitHub
context, or approved change requests into durable requirements in
`docs/requirements.md`. It preserves stable `REQ-*` IDs, records acceptance
and negative criteria, and marks unclear items as open questions instead of
guessing.

### `sdlc-start`

Coordinates the active SDLC run. It reads `docs/requirements.md`,
`docs/design.md`, `active-run.json`, `current-state.json`, feature queue,
fingerprints, latest checkpoint, `STEERING.md`, and evidence. It selects the
highest-priority incomplete feature whose dependencies are satisfied, writes a
checkpoint, and returns exactly one next recommended skill.

### `sdlc-gather-context`

Builds a compact context pack for one `REQ-*` or `FEAT-*`. It gathers official
vendor facts first, internal/project facts second, and code/test evidence
third. The context pack records sources, constraints, unresolved risks, and
design implications without dumping raw pages, threads, or logs.

### `sdlc-create-design`

Converts requirements and gathered context into `docs/design.md`. It maps
stable `REQ-*` blocks to stable `FEAT-*` blocks, defines architecture,
boundaries, data flow, control flow, state, error handling, security,
observability, validation, tests, evaluation, rollback, and done criteria.

### `sdlc-create-plan`

Creates a private, locked execution plan for exactly one ready feature. Plans
live under the local run directory, not in the repository. Existing locked
plans are never edited in place; changed design or context creates a new plan
version.

### `sdlc-tdd`

Defines success before implementation. It writes or maps tests from
requirements, feature design, and the locked plan, then records expected red or
already-green evidence. It does not implement production behavior or weaken
acceptance criteria.

### `sdlc-implement-plan`

Implements production code for the current feature only. It rereads the locked
plan, feature design, context pack, source files, and tests, then makes the
smallest coherent implementation inside the plan boundaries.

### `sdlc-validate-codes`

Checks whether the implementation can build, parse, lint, type-check, import,
and validate configuration. It records validation evidence in the local run
state and classifies failures instead of treating missing or broken tooling as
success.

### `sdlc-unit-tests`

Runs behavior tests for the current feature, including unit, integration,
component, contract, regression, and mock-based tests when applicable. It
records test evidence and classifies failures as implementation, test,
environment, or design defects.

### `sdlc-evaluate`

Observes product behavior against acceptance and negative criteria after
validation and tests pass. It chooses the correct route for the product shape:
`sdlc-gui-test`, `sdlc-tui-test`, API/service checks, performance checks, or
manual review. Passing tests alone are not treated as evaluation.

### `sdlc-gui-test`

Controls and observes browser UI behavior when the evaluation plan requires
GUI evidence. It uses Browser or Playwright MCP when available, captures
screenshots or accessibility snapshots, and records pass/fail results against
acceptance criteria.

### `sdlc-tui-test`

Controls terminal, CLI wizard, or TUI behavior with transcript and exit-code
evidence. It validates prompts, inputs, outputs, side effects, and acceptance
criteria without storing credentials in transcripts.

### `sdlc-classify-failure`

Classifies failed phases before the loop retries. It identifies the primary
failure cause, maps it to the earliest responsible phase, updates retry counts,
and routes back to requirements, context, design, plan, TDD, implementation,
validation, tests, evaluation, UAT, environment, policy, or human input.

### `sdlc-align-specs`

Checks that requirements, design, locked plans, tests, implementation, and
evidence tell one consistent story. It is SDLC-specific and does not replace
the general `align` skill. Drift is routed to the responsible SDLC skill.

### `sdlc-commit`

Creates a local feature-scoped Git commit after validation, tests, and
evaluation pass. It writes short-lived commit authorization immediately before
the guarded commit action, verifies private run state is not staged, records
commit evidence, and never pushes.

### `sdlc-uat-tests`

Runs product-level UAT after all feature commits are complete. It builds a UAT
matrix from requirements, validates cross-feature user journeys and negative
criteria, records evidence, and marks the product ready for `create-pr` only
on pass.

### `create-pr`

Reuses the existing PR creation skill as the SDLC PR handoff. In an SDLC run,
it creates or reuses a PR only after UAT evidence says the product is ready,
unless the user explicitly requests an early draft PR. It records PR evidence
when local run state is available.

### `review-pr`

Reuses the existing PR review skill for merge-readiness review. For SDLC PRs,
it checks the PR against requirements, design, local validation, tests,
evaluation, UAT, and commit evidence when available, while keeping normal PR
review priorities such as correctness, regressions, missing tests, checks, and
conflicts.

### `sdlc-merge-pr`

Merges only after an explicit user request for a specific PR. It verifies PR
status, checks, reviews, branch state, and UAT evidence, writes short-lived
merge authorization immediately before the guarded merge action, records merge
evidence, and marks the run complete when applicable.

## Failure And Retry Model

The loop does not retry blindly. A failing validation, test, evaluation, UAT,
policy, or PR step is routed through `sdlc-classify-failure` so the state
records the failure class, responsible phase, retry count, next skill, and any
human blocker.

Typical routes are:

- spec gaps -> `sdlc-create-requirements`
- missing or conflicting context -> `sdlc-gather-context`
- design defects -> `sdlc-create-design`
- plan defects -> `sdlc-create-plan`
- wrong or missing tests -> `sdlc-tdd`
- implementation defects -> `sdlc-implement-plan`
- validation defects -> `sdlc-validate-codes` after repair
- behavior or acceptance defects -> `sdlc-evaluate` or the correct evaluator
- environment defects -> stop or request the minimum missing setup
- policy blocks -> stop unless the required explicit authorization exists

Retry budgets live in local run state. When a blocker needs human input or the
retry budget is exceeded, the run stops instead of guessing.

## Guarded Git Actions

Git actions are intentionally split:

- `sdlc-commit` creates local feature commits and never pushes.
- `create-pr` pushes and opens or reuses a PR after UAT is ready.
- `review-pr` reviews and may safely fix a writable PR branch.
- `sdlc-merge-pr` merges only with explicit user instruction.

The optional PreToolUse hook can require short-lived local authorization files
under the active run's `permissions/` directory:

- `commit-authorization.json`
- `pr-authorization.json`
- `merge-authorization.json`

These files are written immediately before the guarded action, scoped to the
branch or PR, include an expiry, and are removed or allowed to expire after the
attempt.

## Resume And Idempotency

The workflow is designed to survive conversation loss and repeated resumes.
`sdlc-start` reads the latest checkpoint and current state before selecting a
phase. If nothing changed, it returns the same feature and next recommended
skill without duplicating history. If partial writes occur, it repairs state
from the newest complete checkpoint that matches evidence.

Stable IDs are part of the design:

- `REQ-*` IDs are stable and are not renumbered.
- `FEAT-*` IDs are stable and are not renumbered.
- Locked plan files are immutable; new information creates a new plan version.
- Evidence is refreshed or appended rather than silently erased.

## Boundaries

The workflow intentionally does not:

- create a workflow CLI
- let hooks choose phases or implement workflow logic
- use conversation memory as authoritative run state
- commit local SDLC run state, screenshots, transcripts, or plans
- merge without explicit user instruction
- bypass validation, tests, evaluation, UAT, PR review, or failure
  classification

This keeps the SDLC loop agentic while preserving recoverability, auditability,
and a clear separation between product truth, private execution state, external
capabilities, and runtime guardrails.
