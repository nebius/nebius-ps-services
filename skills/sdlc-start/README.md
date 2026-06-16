# Start SDLC

`sdlc-start` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Coordinate the SDLC loop by reading specs, checkpoints, and local state,
selecting the next feature, and choosing exactly one next skill.

## Main Boundaries

- Free-edit requirements or design.
- Implement code directly.
- Commit, push, create PRs, review PRs, or merge.
- Bypass validation, tests, or evaluation.

## Primary Inputs

- `docs/requirements.md`.
- `docs/design.md` when present.
- Existing local run state when present.
- User instruction or continuation prompt.

## Output

- Active run state is accurate and backed by a checkpoint.
- Current feature and next skill are explicit.
- Each state transition writes a checkpoint and history entry.
- Repeated resumes without state changes do not duplicate history.
- The loop can resume after context loss.
