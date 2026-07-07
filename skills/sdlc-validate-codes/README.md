# Validate Codes

`sdlc-validate-codes` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Answer whether the implemented feature can be built or run correctly and has
passed review-only implementation-quality checks before deeper behavior tests.
It runs project-native mechanical validation first, then uses `code-review`
for the current feature diff and records that review decision in validation
evidence.

## Main Boundaries

- Do not change requirements or design.
- Do not skip configured validation.
- Do not treat missing tooling as success.
- Do not use `code-review` to edit source code from this phase.
- Do not mark validation passed when `code-review` requests changes, owner
  review, more context, or cannot run.
- Do not overwrite test or evaluation evidence.

## Primary Inputs

- Current feature ID.
- Locked plan.
- Changed files.
- Project tooling.
- Relevant design and test context for `code-review`.

## Output

- Validation evidence exists.
- Required checks pass or blocker is classified.
- `code-review` decision is recorded.
- State moves to `validated` only when mechanical validation passes and
  `code-review` runs without requesting changes or owner review.
