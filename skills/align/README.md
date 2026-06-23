# Align

`align` is the repository consistency skill. Use it when a project needs a
senior review-and-repair pass across implementation, wiring, tests, CI,
configuration, CLI behavior, help text, documentation, and changelog entries.

## What It Does

- Inspects the actual project contract before editing.
- Synthesizes the active thread, relevant Agent Memory, and task-state context
  before deciding what to align.
- Finds mismatches between code, tests, workflows, docs, and examples.
- Applies small, evidence-backed fixes instead of broad rewrites.
- Keeps behavior changes aligned with tests and user-facing documentation.
- Reports remaining uncertainty instead of guessing.

## Architecture

```text
User asks for alignment
  |
  v
Gather current thread, memory, and related state context
  |
  v
Map the relevant project contract
  |
  v
Compare code, tests, docs, workflows, and config
  |
  v
Patch confirmed inconsistencies
  |
  v
Run focused validation and report residual risk
```

## Workflow

1. Consolidate the latest user request, current thread, relevant Agent Memory,
   and related task or workflow state.
2. Inspect the relevant codebase surfaces before changing anything.
3. Establish the intended behavior from nearby evidence.
4. Prioritize real bugs, broken wiring, stale docs, missing tests, and unsafe
   assumptions.
5. Patch the smallest responsible surface.
6. Update tests, docs, examples, help text, and changelog entries when they are
   affected.
7. Validate with focused commands first, then broader checks when appropriate.

## Core Concepts

- Evidence beats speculation.
- Memory and task state are decision inputs, not proof until verified against
  current repository or runtime evidence.
- Preserve intended behavior unless a bug or stale contract is proven.
- Prefer one canonical path over compatibility shims unless requested.
- Keep edits easy to review.

## Files

- `SKILL.md`: runtime alignment workflow and guardrails.
- `agents/openai.yaml`: UI metadata and invocation prompt.
