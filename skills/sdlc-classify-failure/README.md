# Classify Failure

`sdlc-classify-failure` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Classify failures and select the correct retry or stop route before any SDLC loop retry.

## Main Boundaries

- Retry without classification.
- Overwrite old failure history.
- Persist raw secrets, logs, or private data.
- Route to merge or PR creation.

## Primary Inputs

- Current feature and phase.
- Failure evidence.
- Retry counts.
- Latest validation, test, evaluation, UAT, or policy output.

## Output

- Failure class is recorded.
- Next skill or blocker is explicit.
- Retry budget state is updated.
