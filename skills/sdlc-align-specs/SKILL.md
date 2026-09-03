---
name: sdlc-align-specs
description: "Use only as part of the Agentic SDLC workflow; when explicitly requested, produce an advisory spec-consistency report that never gates phases, commit, or completion. Use align for required changed-surface alignment."
---

# Align Specs

## Help

For `$sdlc-align-specs --help` or `$sdlc-align-specs -h`, return concise help and stop before
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

## Purpose

Produce an optional advisory report on whether SDLC specs, plans,
implementation, documentation, tests, and evidence tell one consistent story.

## When To Use

- The user explicitly asks for the legacy SDLC-specific consistency view.
- A failure suggests drift between requirements, design, tests, implementation,
  documentation, steering, or evidence.
- The user asks for SDLC spec alignment.

## When Not To Use

- Do not use for general project-wide alignment outside SDLC artifacts; use `align`.
- Do not rewrite requirements or design directly.
- Do not implement broad fixes without routing to the responsible skill.

## Inputs

- Requirements, design, current feature plan, changed files, tests,
  documentation, steering, and evidence. Lifecycle observations are optional.
- Current feature ID or full-run scope.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- The effective selected-project instruction chain. Lifecycle state may be read
  as advisory context when present.
- Locked plans.
- Validation, test, evaluation, documentation, UAT, and commit evidence.
- Changed implementation and tests.
- Project-facing docs touched by the active feature or run.

## Writes

- Alignment report.
- Failure classification or next recommended skill when drift is found.

## Process

- Resolve the registered integration worktree and verify its branch, Git common
  directory, and recorded HEAD before alignment. Review code, tests, and docs
  from that checkout; do not use the stale project checkout as feature truth.
- Map `REQ-*` to `FEAT-*`, plans, tests, implementation, documentation, and
  evidence.
- For features with a vertical flow or layer map, check that `docs/design.md`,
  the locked plan's End-To-End Slice, TDD/tests, implementation boundaries,
  validation evidence, evaluation evidence, documentation updates, and UAT
  evidence all describe the same slice or explicitly record why no slice
  applies.
- Check stable IDs and status fields.
- Check that no spec publication bypassed the `maintain-project-specs` paired
  transaction or canonical validation contract.
- Report lifecycle or project-instruction drift as advisory context. Never make
  it a prerequisite for alignment, commit readiness, or workflow completion.
- Check that evidence supports claimed state transitions.
- Check that project-facing docs do not describe behavior that implementation
  and evaluation evidence have not proven.
- Classify drift and route to the responsible SDLC skill.

## Idempotency

- Rerunning alignment updates the report without duplicating findings.
- Unchanged specs and evidence should produce stable conclusions.
- Resolved drift should be marked resolved instead of erased from history.

## Failure Handling

- Missing requirements or design routes to their owner skills.
- Missing evidence routes to the responsible phase.
- Spec contradiction maps to `SPEC_GAP`.
- Plan/design mismatch maps to `DESIGN_DEFECT` or `PLAN_DEFECT`.
- Slice mismatch maps to the earliest owner: design for wrong layer map, plan
  for wrong slice, TDD/tests for missing coverage, implementation for
  out-of-scope files, evaluation for missing observation, or documentation for
  unsupported wording.
- Documentation mismatch maps to `DOCUMENTATION_DRIFT` and routes to
  `sdlc-update-documents`.
- Stale, missing, or invalid historical lifecycle-phase evidence is advisory.
  An explicit project-instruction mutation request routes to
  `project-agent-instructions`.

## Must Not

- Free-edit requirements or design.
- Edit any project instruction file; route it to
  `project-agent-instructions`.
- Modify locked plans.
- Pretend unchecked evidence passed.
- Replace the general `align` workflow.

## Completion Criteria

- Alignment report exists.
- Each drift item has an owner skill or blocker.
- Vertical flow, layer map, locked slice, and evidence agree when applicable.
- Unresolved inconsistencies are reported with owners, without changing commit
  or workflow readiness.
- Any lifecycle observation is labeled advisory and does not affect the
  alignment verdict.

## SDLC Invariants

- Treat `docs/requirements.md`, `docs/design.md`, and every already-effective
  selected-project instruction file as project truth.
- `maintain-project-specs` is the sole semantic, schema, paired-publication,
  migration, and validation owner for both canonical specs. Agentic SDLC
  requirement and design skills are phase adapters over that contract. Explicit
  project-instruction mutation stays outside the automatic SDLC workflow.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different SDLC shape.
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
