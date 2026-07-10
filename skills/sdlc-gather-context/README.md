# Gather Context

`sdlc-gather-context` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Gather only the context needed for one feature and produce a compact context
pack for design and planning, including layer and boundary facts when the
feature spans a vertical slice.

## Main Boundaries

- Implement code.
- Create design decisions without evidence.
- Store secrets or private tokens in context packs.
- Use unofficial sources when official docs are available.

## Primary Inputs

- One `REQ-*` or `FEAT-*`.
- Relevant requirement or feature block.
- User-provided links or references.
- Existing design block when present.
- Layer contracts, source files, tests, and integration seams when the feature
  crosses serial application layers.

## Output

- Context pack exists.
- Important facts have source traceability.
- Layer and boundary facts are captured when applicable.
- Design implications and open blockers are explicit.
