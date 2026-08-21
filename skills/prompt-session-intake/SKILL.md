---
name: prompt-session-intake
description: "Internal coordinator only for safe direct-prompt metadata bound to Task Implementer or Agentic SDLC. Extract durable intent, record merge/noop/sensitive, merge once, and consume the sidecar; never invoke standalone."
---

# Prompt Session Intake

## Help

For `$prompt-session-intake --help` or `$prompt-session-intake -h`, return
concise help and stop before any workflow step. State the purpose and invocation
policy. Show exact usage for every public action. Describe each public action,
positional argument, and flag in one concise line, including `-h, --help`; say
"No additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Bridge a non-blocking `UserPromptSubmit` capture sidecar to the existing prompt
owners. The current agent always handles the delivered request normally. When
safe capture succeeds, only that agent can select a project-intent projection and only
the owning Task Implementer or Agentic SDLC prompt adapter can update the
canonical objective. Capture never selects, starts, or resumes a workflow.

## Invocation Policy

Use only when this skill's hook adds current-turn context containing one exact
private event path and acceptance token. Keep
`policy.allow_implicit_invocation: false`. There is no public binding, accept,
consume, replay, or recovery command.

## When To Use

- Use only for the exact current root-agent turn when this skill's capture hook
  supplies its private event path and acceptance token.

## When Not To Use

- Do not use for an unbound direct prompt, explicit workflow command, manual
  file edit, older event, Stop-generated continuation, compaction, system, or
  subagent prompt.

## Inputs

- The current delivered user message, exact hook receipt, bound workflow and
  project, and canonical prompt state when material.

## Required Reads

Read `references/state-contract.md` before any transition.

## Writes

- Metadata-only private event-v2 state, an accepted mode-0600
  `project-intent.md` projection when eligible, binding, current-event receipt,
  one-shot Stop continuation marker, and project objective registry under the
  Codex home. Never write the submitted prompt body or a raw prompt journal.
- Canonical workflow prompts only through the owning Task Implementer or SDLC
  compare-and-set adapter; this skill never writes committed project files.

## Process

1. Confirm the event is for the current delivered root-agent turn and names the
   expected bound workflow. Continue the direct request normally and never
   select an older staged event.
2. Record exactly one disposition: `merge`, `noop`, or `sensitive`. A merge
   also uses exactly one classification: `intent`, `steering`, `constraint`,
   `clarification-answer`, or `acceptance-change`.
3. Use `sensitive` when later inspection finds content that must not remain in
   capture state. Invoke private `accept` with no prompt inputs; the transition
   atomically discards the submitted digest, operation ID, token, and any
   projection authority. Continue the direct request without capture replay.
4. Use `noop` for workflow or skill invocation, shell/tool action, delivery or
   operational control, agent/session/response control, status or inquiry,
   conversation, unrelated content, or already-recorded intent. Record the
   matching no-op reason, then consume without prompt or run identity. Do not
   edit or run a workflow.
5. Use `merge` only for durable project objectives, scope, behavior, API/CLI/
   configuration/schema/data contracts, architecture decisions, constraints,
   acceptance outcomes, domain facts and examples, priorities, non-goals,
   trade-offs, corrections, clarification answers, and rollout or operational
   requirements. For mixed turns, exclude ephemeral skill, workflow, shell,
   tool, delivery, orchestration, status, response-style, and conversation
   clauses. Keep a command only when it declaratively defines a project
   interface, example, or verification contract.
6. Inspect the exact canonical prompt and compute its digest. Put only the
   selected durable project intent in one private mode-0600
   `project-intent.md`. Be concise but lossless for selected facts, values,
   negations, decisions, uncertainty, examples, references, constraints, and
   acceptance outcomes; do not preserve the excluded wrapper.
7. Invoke private `accept` with the exact event/token, `merge` disposition,
   material classification, projection file, canonical prompt path, and base
   digest. For a first objective, use the explicit new-objective transition
   instead of inventing a base.
8. Route to the bound workflow adapter. Rehash and merge the accepted
   projection using compare-and-set, the accepted operation ID, and accepted
   projection digest, without calling its start/resume path. An exact operation
   retry returns the already-applied result. A byte-identical projection under
   another operation returns a terminal duplicate no-op; semantic paraphrase
   detection remains the agent's responsibility.
9. After the canonical merge or exact duplicate result is verified, invoke
   private `consume` with its
   exact full prompt ID, prompt reference, path, and resulting digest. Do not
   supply a run ID or terminal workflow fact from direct capture; carry the
   adapter's duplicate result exactly.
10. If capture cannot stage, accept, merge, or consume, keep handling the direct
   request. Preserve only bounded private transition evidence; never replay the
   prompt or request a Stop continuation solely to repair capture.

## Manual edits

File edits have no trigger. If a user edits a managed prompt directly, require
an explicit `$task-implementer run <prompt-ref-or-file>` or
`$sdlc-start run <prompt-ref-or-file>`. Never turn filesystem observation into
automatic execution.

## Safety

- Skip capture for recognized secrets before any event, projection, or
  canonical prompt write, while allowing the direct request to reach the
  current agent. Never partially capture a mixed secret-bearing prompt.
- Metadata staging stores only bounded identity, digest, workflow, project,
  token, and timing fields. Never persist or reconstruct the submitted body.
- Treat the five-character prompt reference as non-authoritative. Resolve it
  only when exact and unique; persist and compare the full prompt ID.
- Reject stale current-event claims, mismatched current sessions, stale base
  digests, manual drift, projection substitution, cross-workflow claims,
  reused turn identity with changed content, symlinks, unsafe file modes, and ambiguous active objectives
  from capture. Treat writer-session identity as provenance, never an admission
  lease. For Task Implementer, treat manifest-proven primary and managed-lane
  project paths as one logical objective.
- Do not acquire Task Implementer or SDLC workflow locks from the hook. The
  capture hook uses only its own bounded state lock; workflow adapters acquire
  their normal locks after acceptance.
- Use the shared Stop arbiter delegate. Never register an independent Stop
  hook for prompt intake, and never block Stop because capture is incomplete or
  invalid.
- Explicit workflow invocations carrying a binding receipt register their
  active prompt result through the private objective transition; terminal
  workflow results close it. Never infer active or terminal state from a file
  save, prompt text, or an unverified run result.

## Idempotency

The same session/turn/content stages once in the event-v2 namespace. Event-v1
records and raw journals remain inert and are never read, migrated, rewritten,
or deleted. Acceptance retries must reproduce the exact disposition,
classification or reason, private projection, base, and new-objective choice.
The prompt adapter applies one operation-and-projection binding once, and
consumption retries must reproduce the exact result identity before returning the terminal state.
Reused turn identity with different content, stale base digests, and divergent
capture retries fail closed for capture without affecting the direct request.

## Failure Handling

Leave a failed material event staged or accepted with its bounded structured
capture evidence and continue the direct request. Do not replay an older event,
silently reclassify it, retry through a second path, or consume before prompt
postconditions are authoritative. The prompt-session Stop delegate always
passes; it never requests continuation for capture recovery.

## Must Not

- Do not expose private paths, tokens, prompt bodies, session identifiers, or
  digests that are not part of the bounded public result.
- Do not let the hook process refine text, edit canonical prompts, select work,
  or execute either workflow.
- Do not install/register hooks, change trust, restart Codex, or claim runtime
  activation unless those separate actions are explicitly authorized.

## Completion Criteria

The direct request proceeds whether capture is consumed, skipped, or remains
staged/accepted with a structured blocker. A successful material sidecar
updates the canonical prompt exactly once without starting a workflow. Report
source, installation, registration/trust, restart, and fresh-session proof
separately.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state. This also excludes raw
or project-intent projections, event paths, tokens, and session identifiers.

## Output Contract

Return only the disposition, material classification or no-op reason, bound
workflow, capture transition outcome, resulting prompt reference when merged,
and a structured capture blocker when
incomplete. Never return raw prompt text, event paths, acceptance tokens, full
session identifiers, or private state roots.
