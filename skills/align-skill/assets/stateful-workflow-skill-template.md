---
name: {skill-name}
description: "{Front-load the workflow trigger, accepted inputs, state boundary, and adjacent-skill boundary.}"
---

# {Skill Name}

## Purpose

{One concise statement of the stateful workflow step this skill owns.}

## When To Use

- {Concrete user or coordinator prompt that should trigger this skill.}

## When Not To Use

- {Adjacent workflow, simpler skill, or unsafe condition that should not trigger this skill.}

## Inputs

- {Prompt, spec, state file, artifact, or external context accepted by this skill.}

## Required Reads

- {Files, local state, docs, references, or MCP resources that must be read first.}

## Writes

- {Committed files, local private state, evidence, or external artifacts this skill may write.}

## Process

- {Ordered workflow step.}

## Idempotency

- {How reruns avoid duplicate state, repeated side effects, or stale evidence.}

## Failure Handling

- {How failures are classified, retried, routed, or stopped for human input.}

## Must Not

- {Destructive, unsafe, out-of-scope, or adjacent-skill behavior to avoid.}

## Completion Criteria

- {Observable final state that means this skill is done.}

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Output Contract

Return:

- Scope handled.
- State or artifacts written.
- Evidence created or checked.
- Failure classification and next action when blocked.
