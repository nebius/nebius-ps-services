---
name: sdlc-gather-context
description: "Use only as part of the Agentic SDLC workflow; use when an Agentic SDLC feature needs technical, product, vendor, internal, codebase, layer, or test context before design or implementation. Produces a compact feature-specific context pack."
---

# Gather Context

## Purpose

Gather only the context needed for one feature and produce a compact context pack for design and planning.

## When To Use

- A `REQ-*` or `FEAT-*` needs technical context before design.
- Vendor, internal, codebase, or test facts may affect implementation.
- A design or plan is blocked by missing context.

## When Not To Use

- Do not use to make design decisions without evidence.
- Do not use to implement code.
- Do not use to dump raw webpages, Slack threads, or logs.

## Inputs

- One `REQ-*` or `FEAT-*`.
- Relevant requirement or feature block.
- User-provided links or references.
- Existing design block when present.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md` if present.
- Relevant source files, tests, README, architecture docs, and layer boundary
  contracts when the feature spans serial application layers.
- Official vendor docs first.
- Internal Confluence, Slack, Jira, GitHub, or MCP resources when available.

## Writes

- `context/FEAT-*.context.md`.
- Context fingerprint, source list, and open context questions.

## Process

- Use `assets/templates/context-pack.md.template` when creating a context pack.
- Identify technologies and vendors used by the feature.
- Gather official vendor facts first, internal docs second, code and tests third.
- Summarize only facts that affect design or implementation.
- For serial multi-layer features, gather the facts needed to design one
  vertical slice: existing layer owners, API or command contracts, data or
  persistence contracts, boundary contracts, fixtures, integration seams, and
  observed gaps between layers.
- Record exact versions, APIs, constraints, unresolved risks, and design implications.

## Idempotency

- If context sources and feature requirements are unchanged, reuse the context pack.
- If sources changed, update the context pack and mark affected design blocks stale.

## Failure Handling

- If official docs are unavailable, state that the behavior could not be verified.
- If internal docs conflict with vendor docs, record the conflict and prefer official docs unless internal platform behavior overrides it.
- If context is insufficient, mark `CONTEXT_GAP`.

## Must Not

- Implement code.
- Create design decisions without evidence.
- Store secrets or private tokens in context packs.
- Use unofficial sources when official docs are available.

## Completion Criteria

- Context pack exists.
- Important facts have source traceability.
- Layer and boundary facts are recorded when the feature likely spans a
  vertical slice.
- Design implications and open blockers are explicit.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only `sdlc-create-design`
  writes `docs/design.md`. Other skills route spec changes to those owners.
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

- Use `assets/templates/context-pack.md.template` when creating the corresponding artifact.
