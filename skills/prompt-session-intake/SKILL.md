---
name: prompt-session-intake
description: "Internal coordinator used only when its capture hook routes a safe direct prompt from an explicitly bound Task Implementer or Agentic SDLC session. Classify, losslessly refine, accept, merge, execute once, and consume the exact staged turn; never invoke standalone or for unbound, Stop-generated, compaction, system, or subagent prompts."
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

Bridge a capture-only `UserPromptSubmit` hook to the existing semantic and
execution owners. The hook can stage exact private provenance and add context;
only the current agent can classify/refine the delivered prompt and only Task
Implementer or Agentic SDLC can update and run its canonical objective.

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
  project, canonical prompt state when material, and authoritative workflow
  result needed for consumption.

## Required Reads

Read `references/state-contract.md` before any transition.

## Writes

- Private mode-0600 raw/refined journals, event state, binding, one-shot Stop
  continuation marker, and project objective registry under the Codex home.
- Canonical workflow prompts only through the owning Task Implementer or SDLC
  compare-and-set adapter; this skill never writes committed project files.

## Process

1. Confirm the event is for the current delivered root-agent turn and names the
   expected bound workflow. Never select an older staged event.
2. Classify the turn as exactly one of `intent`, `steering`, `constraint`,
   `clarification-answer`, `acceptance-change`, `conversation`, `status`, or
   `control`.
3. For a nonmaterial class, invoke private `accept` without prompt inputs, then
   private `consume` without prompt or run identity. Continue the conversation
   normally; do not edit or run a workflow.
4. For a material class, inspect the exact current canonical prompt and compute
   its digest. Refine the current user message for grammar, semantic clarity,
   and concision without deleting facts, data, constraints, decisions,
   uncertainty, references, or acceptance intent. Put only that lossless
   refinement in one private mode-0600 file.
5. Invoke private `accept` with the exact event/token, class, refined file,
   canonical prompt path, and base digest. For a first objective, use the
   explicit new-objective transition instead of inventing a base.
6. Route to the bound workflow adapter. Merge the accepted refinement into the
   objective-owned prompt using compare-and-set and the accepted operation ID,
   then continue the workflow's same canonical start/resume path used by
   explicit `run`. On recovery, reuse that exact operation ID; the adapter must
   return the one already-applied result instead of merging twice.
7. After the workflow has started or resumed exactly once, invoke private
   `consume` with its exact full prompt ID, prompt reference, path, resulting
   digest, run ID when one exists, and terminal objective fact only when the
   workflow authoritatively completed. Do not consume before that transition.
8. If any peer hook blocks prompt submission, leave this event staged. It is
   ineligible for later implicit replay; a later turn uses only its own event.

## Manual edits

File edits have no trigger. If a user edits a managed prompt directly, require
an explicit `$task-implementer run <prompt-ref-or-file>` or
`$sdlc-start run <prompt-ref-or-file>`. Never turn filesystem observation into
automatic execution.

## Safety

- Reject recognized secrets before any raw, refined, or canonical prompt write.
- Keep the raw session journal private and lossless; never quote it in hook
  diagnostics, logs, task state, docs, or final responses.
- Treat the five-character prompt reference as non-authoritative. Resolve it
  only when exact and unique; persist and compare the full prompt ID.
- Reject stale base digests, manual drift, a second writer, cross-workflow
  claims, duplicate turn content, symlinks, unsafe file modes, and ambiguous
  active objectives.
- Do not acquire Task Implementer or SDLC workflow locks from the hook. The
  capture hook uses only its own bounded state lock; workflow adapters acquire
  their normal locks after acceptance.
- Use the shared Stop arbiter delegate. Never register an independent Stop
  hook for prompt intake.
- Explicit workflow invocations carrying a binding receipt register their
  active prompt result through the private objective transition; terminal
  workflow results close it. Never infer active or terminal state from a file
  save, prompt text, or an unverified run result.

## Idempotency

The same session/turn/content stages once. Acceptance retries must reproduce
the exact classification, private refinement, base, and new-objective choice.
The workflow adapter applies one operation ID once, and consumption retries
must reproduce the exact result identity before returning the terminal state.
Reused turn identity with different content, stale base digests, and divergent
execution retries fail closed.

## Failure Handling

Leave a failed material event staged or accepted with its structured blocker.
Do not replay an older event, silently reclassify it, retry through a second
workflow path, or consume before prompt/run postconditions are authoritative.
The shared Stop arbiter requests continuation only for the exact unfinished
current event.

## Must Not

- Do not expose private paths, tokens, prompt bodies, session identifiers, or
  digests that are not part of the bounded public result.
- Do not let the hook process refine text, edit canonical prompts, select work,
  or execute either workflow.
- Do not install/register hooks, change trust, restart Codex, or claim runtime
  activation unless those separate actions are explicitly authorized.

## Completion Criteria

The exact current event is consumed once, or it remains visibly staged/accepted
with a structured blocker. Report source, installation, registration/trust,
restart, and fresh-session proof separately.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings in the
narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state. This also excludes raw
or refined prompts, event paths, tokens, and session identifiers.

## Output Contract

Return only the classification, bound workflow, transition outcome, resulting
prompt reference when material, run/resume outcome, and a structured blocker
when incomplete. Never return raw prompt text, event paths, acceptance tokens,
full session identifiers, or private state roots.
