---
name: sdlc-create-requirements
description: "Use only as part of the Agentic SDLC workflow; adapt user intent into canonical REQ records in docs/requirements.md through maintain-project-specs, preserving stable IDs and shared-owner validation."
---

# Create Requirements

## Help

For `$sdlc-create-requirements --help` or `$sdlc-create-requirements -h`, return concise help and stop before
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

Convert user intent into durable, testable product requirements in `docs/requirements.md`.

## When To Use

- A project idea, ticket, issue, Slack thread, Confluence page, or rough prompt needs requirements.
- An existing requirement needs an approved update from user feedback.
- An active SDLC run discovers a spec gap that must be reflected in requirements.

## When Not To Use

- Do not use for design details; use `sdlc-create-design`.
- Do not use for local execution plans; use `sdlc-create-plan`.
- Do not use for implementation, validation, tests, UAT, PR, or merge work.

## Inputs

- The exact immutable prompt revision accepted by `sdlc-start`, or an approved
  change request routed from its steering disposition.
- Existing `docs/requirements.md` when present.
- Existing `docs/design.md` for impact awareness only.
- Optional live experiment environment details, including safe connection and
  usage instructions.
- Optional Jira, Slack, Confluence, GitHub, or pasted context.

## Required Reads

- `references/prompt-refinement.md` for prompt-v2 extraction, source
  precedence, selective clarification, and stable question handling.
- Existing requirements file.
- Existing design file if present.
- Project README or docs if available.
- Active SDLC run state if the change happens during a run.
- The bound run's `prompt.json` and accepted snapshot when prompt intake
  initiated the change.
- The bound run's private `requirements-refinement.json` when prompt-v2 intake
  initiated or revised the objective.

## Writes

- The requirements managed region through the `maintain-project-specs` paired
  publisher. The existing design bytes participate in validation and
  compare-and-set even when this adapter does not change them.
- A local run-history change summary when an SDLC run is active.

## Process

- Use `assets/templates/requirements.md.template` when creating the file.
- Extract the entire Ask and all optional/custom headings into product goal,
  users, actors, inputs/outputs, context, functional behavior, constraints,
  acceptance and negative criteria, verification/test/evaluation, non-goals,
  assumptions, dependencies, references, and external systems. The user does
  not need to pre-sort their Ask into those sections.
- Inspect safely discoverable repository facts before asking questions. Ask
  only for ambiguity that materially changes product behavior, interfaces,
  safety/authority, architecture, cost/availability, acceptance evidence, or
  deletion of accepted truth. Persist stable `Q-*` IDs and answer provenance
  privately; a conflicting later revision reopens the same ID.
- Ask for or record the optional Live Experiment Environment when the user can
  provide one: status, environment type, non-production confirmation, safe
  access reference, connection steps, allowed and prohibited agent actions, test
  data, reset process, approvals, and evidence rules.
- If a user provides raw credentials, private endpoints, customer data, or
  sensitive logs, replace them with placeholders or safe references and ask for
  an approved credential delivery mechanism instead of committing the values.
- Break intent into stable `REQ-*` blocks with acceptance and negative criteria.
- Add inputs, outputs, validation method, test method, evaluation method, priority, and risk to each requirement.
- Preserve existing requirement IDs and append new IDs instead of renumbering.
- Update the requirements decision log and change log.
- Mark unclear items as open questions instead of guessing.
- Preserve accepted product truth on omission. Remove or supersede it only from
  explicit user intent. Treat an edited completed prompt as a fresh full
  objective evaluated against current truth, not as a textual patch.
- Before publication, ensure every active requirement has a covering design or
  remains explicitly pending for `sdlc-create-design`. Publish no unilateral
  one-file transaction. After paired validation/publication, bind the exact v2
  receipt in the private refinement ledger and set `ready` only when no
  material question is open or reopened.
- Invoke the private `refinement-verify` action owned by `sdlc-start` with the
  exact workspace and run after saving `ready` and a complete private impact
  claim. Route to design only when the shared owner proves that the latest
  accepted prompt identity and intent, every extracted statement occurrence,
  and the exact current requirements/design bytes have one accepted impact
  receipt. Do not use matching bytes or a bare `no_effect` label as proof.

## Idempotency

- Reapplying the same prompt revision must not duplicate requirements.
- If an existing requirement changes, update that `REQ-*` block and append a change-log entry.
- If design must change, mark the affected requirements so `sdlc-create-design` can update related features.
- When execution is already prepared, write product truth only in the registered
  integration worktree, mark the active plan/execution `REPLAN_REQUIRED`, and
  preserve every started assignment, commit, and worktree for coordinator-led
  reconciliation. Do not reset execution history.

## Failure Handling

- If ambiguity is non-blocking, create draft requirements and list open questions.
- If ambiguity blocks meaningful progress, stop with the required questions.
- If the existing requirements file is malformed, repair structure without changing intent.

## Must Not

- Change the design managed region. Passing its exact existing bytes to the
  paired publisher is required and is not a design edit.
- Create execution plans.
- Implement code or tests.
- Rename existing requirement IDs.
- Delete accepted requirements without explicit user instruction.
- Store secrets, private endpoints, customer data, or raw logs in
  `docs/requirements.md`.
- Mark a live experiment environment safe unless non-production or disposable
  scope and allowed operations are explicit.

## Completion Criteria

- `docs/requirements.md` exists.
- Every requirement has acceptance criteria, validation method, test method, and evaluation method.
- The Live Experiment Environment section has a status; when provided, it
  records safe access references, allowed operations, reset instructions, and
  evidence limits.
- Open questions and change log are explicit.
- The private refinement verifier passes for the latest accepted revision,
  complete statement-impact claim, and exact current canonical specs.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- `maintain-project-specs` owns both canonical documents and their paired
  transaction. This skill may change requirements only while routed as its
  Agentic SDLC authoring adapter;
  `sdlc-create-design` has the corresponding design adapter boundary.
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

- Use `assets/templates/requirements.md.template` when creating the corresponding artifact.
