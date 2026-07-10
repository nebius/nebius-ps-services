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

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Scope handled.
- State or artifacts written.
- Evidence created or checked.
- Failure classification and next action when blocked.
