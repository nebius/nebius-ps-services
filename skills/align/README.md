# Align

`align` is the repository consistency skill. Use it when a project needs a
senior review-and-repair pass across implementation, wiring, tests, CI,
configuration, CLI behavior, help text, documentation, and changelog entries.

## What It Does

- Inspects the actual project contract before editing.
- Finds mismatches between code, tests, workflows, docs, and examples.
- Applies small, evidence-backed fixes instead of broad rewrites.
- Keeps behavior changes aligned with tests and user-facing documentation.
- Reports remaining uncertainty instead of guessing.

## Architecture

```text
User asks for alignment
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

1. Inspect the relevant codebase surfaces before changing anything.
2. Establish the intended behavior from nearby evidence.
3. Prioritize real bugs, broken wiring, stale docs, missing tests, and unsafe
   assumptions.
4. Patch the smallest responsible surface.
5. Update tests, docs, examples, help text, and changelog entries when they are
   affected.
6. Validate with focused commands first, then broader checks when appropriate.

## Core Concepts

- Evidence beats speculation.
- Preserve intended behavior unless a bug or stale contract is proven.
- Prefer one canonical path over compatibility shims unless requested.
- Keep edits easy to review.

## Files

- `SKILL.md`: runtime alignment workflow and guardrails.
- `agents/openai.yaml`: UI metadata and invocation prompt.
