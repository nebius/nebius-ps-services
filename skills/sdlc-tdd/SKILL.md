---
name: sdlc-tdd
description: "Use only as part of the Agentic SDLC workflow; after sdlc-prepare-execution, write feature tests in the registered integration worktree before dependency-wave implementation."
user-invocable: false
---

# SDLC TDD

## Help

For `$sdlc-tdd --help` or `$sdlc-tdd -h` (including native Claude forms), return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Agent Compatibility

Before workflow reads, load `../global-context-management/references/agent-hosts.md`
for the selected agent home, native identity and required runtime setup.

Use `$sdlc-tdd` in Codex, `/sdlc-tdd` in Claude Code, or
`/skills:sdlc-tdd` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Coordinator Invocation Boundary

This skill requires verified workflow context from the active Agentic SDLC
coordinator. Before acting, verify the selected project, active run, phase and
coordinator ownership through the workflow's existing state helpers. Without
that evidence, return without mutation; a skill mention or automatic selection
is not workflow authorization. In Claude Code this skill is hidden from the
public command menu but remains available to the verified coordinator.

## Purpose

Define success before implementation by creating tests that prove the current feature behavior.

## When To Use

- A locked feature plan and verified `execution_prepared` state exist, and tests
  must be written in the registered integration worktree before implementation.
- Acceptance criteria need unit, integration, component, contract, or regression coverage.
- Existing tests must be mapped to SDLC acceptance criteria.
- A counted active-task `TEST_DEFECT` requires guarded test revision after the
  original TDD seal; follow the late-repair path below.

## When Not To Use

- Do not use to implement production behavior.
- Do not use to weaken acceptance criteria.
- Do not use when no locked plan exists.

## Inputs

- Locked feature plan.
- Feature design.
- Requirement acceptance criteria.
- Existing test conventions.
- Verified integration worktree, branch, base SHA, plan digest, and Git common
  directory from private execution state.

## Required Reads

- `../sdlc-start/references/state-schema.md` for the root checkpoint fields;
  phase output labels belong in `current_phase`, not the run's `status`.

- `../worktree/references/workflow-invariants.md` for shared ownership,
  evidence, failure and conditional-read rules.
- `../worktree/references/workflow-contract.md` for execution lifecycle operations.
- Read `../worktree/references/document-delivery.md` when accepted inputs include
  a source manifest/program, this phase handles SLICE/source coverage or phase
  barriers, or it handles delivery admission, phase exit or protected completion.

- Locked plan.
- Feature and requirement blocks.
- Existing tests and test framework configuration.
- The execution-plane reference owned by `sdlc-prepare-execution` and the
  active coordinator record.

## Writes

- Tests, fixtures, or mocks required by the plan.
- Test evidence showing expected red or already-green state.
- Local checkpoint with `current_phase: tests_red` or `tests_ready` and
  `status: running`.

## Process

- Identify acceptance criteria that can be tested.
- Use `integration_worktree / project_scope` as the authoring cwd (the integration
  root when scope is `.`). Re-observe the recorded integration Git root,
  common directory, branch, exact recorded HEAD, and cleanliness. Fail with
  `WORKTREE_CONFLICT` on drift; never write tests in the project checkout.
- Choose the smallest useful test level first.
- When the locked plan defines a planned end-to-end slice, map tests to its
  behavior, layer contracts, and cross-layer validation target. Prefer the
  smallest useful
  unit, contract, component, or integration coverage that would fail if one
  planned layer or boundary is missing.
- Write tests that fail for missing behavior when implementation is absent.
- Avoid over-mocking real behavior.
- Run focused tests and record evidence.
- For initial TDD, publish the outcome in `current_phase`: `tests_red` for missing
  behavior, or `tests_ready` when the reviewed tests already pass. Keep the root
  `status` as `running` and set `next_recommended_skill` to
  `sdlc-implement-plan`. Write the same fields into `current-state.json` and its
  new immutable checkpoint, then update `checkpoints/latest.json`. Preserve
  prior checkpoints and repair counters. During authoring, use
  `current_phase: sdlc-tdd` with `status: running`. Never put `tests_red`,
  `tests_ready` or another phase output label in the root `status` field.
  Classified late test repair follows its separate route below.
- Leave the integration changes uncommitted. `sdlc-implement-plan` seals the
  initial TDD base exactly once before it creates worker branches. This initial
  authoring rule does not authorize reopening a sealed base.

### Classified Late Test Repair

For an active unmerged worker wave, consume the classifier's `test_revision`
route and counted localized dispatch. Read the exact private transition and
review contract in the `Late Test Revision` section of
`../sdlc-prepare-execution/references/execution-plane.md`.
Stop every allocated native writer, prove termination and release runtime leases.
Select only existing tracked tests or test-only fixtures/configuration required
by the proven cause; preserve accepted criteria and independent harnesses.

Prepare the revision through the execution owner before writing. Author only its
frozen files in the registered integration checkout. Review exact before/after
bytes, document why assertions and accepted behavior remain intact, and create
the private review receipt. Seal through `seal-test-revision`; never commit
directly, change an assignment, reopen `seal-tdd` or relabel the cause as design.

Return to planning for an appended corrective task and fresh execution basis.
Preserve the failed worker candidate and original TDD evidence. Reacquire
preparation and TDD observations at the new basis, record the remaining expected
red or already-green state, and rerun every invalidated downstream gate. When
fixture-only correction cannot run without unfinished product code, record that
specific expected red boundary; verify the original failure after fresh worker
implementation. A review or sealed fixture alone never constitutes acceptance.

## Idempotency

- Do not duplicate tests.
- Reuse existing tests that already match criteria.
- If implementation already exists and tests pass, record `tests_already_green`
  in the test evidence and publish the `tests_ready` checkpoint above.

## Failure Handling

- If test framework is missing, route to project scaffolding or design update.
- If criteria are not testable, route to `sdlc-create-requirements`.
- If design lacks test seams, route to `sdlc-create-design`.
- If the planned slice cannot be tested from the available seams, route to
  `sdlc-create-plan` or `sdlc-create-design` instead of replacing it with
  layer-isolated tests only.

## Must Not

- Implement production code except minimal test-only fixtures.
- Delete or weaken tests to pass.
- Skip test execution unless environment is blocked.

## Completion Criteria

- Tests exist and map to acceptance criteria.
- Planned end-to-end slice coverage is present or a plan/design blocker is
  classified.
- Test run evidence exists.
- Expected red or already-green state is recorded.
- Project checkout remains clean and unchanged at the prepared base.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- `maintain-project-specs` is the sole semantic, schema, and validation owner
  of both canonical specs. Inside Agentic SDLC, only its routed
  `sdlc-create-requirements` and `sdlc-create-design` authoring adapters may
  write their respective managed records; all other phase skills route changes
  through those adapters and return validation to the shared owner.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `<agent-home>/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Keep one delivery candidate active; eligible tasks may span features within its frozen slice.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- No skill-local references are required by default; use project files and current official documentation as needed.
