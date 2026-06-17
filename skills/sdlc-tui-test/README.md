# TUI Test

`sdlc-tui-test` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Control terminal applications like a user and capture prompts, inputs, outputs, exit codes, and side effects.

## Main Boundaries

- Hide interactive failures.
- Treat a zero exit code as sufficient when criteria require specific behavior.
- Persist credentials in transcripts.

## Primary Inputs

- TUI or CLI acceptance criteria.
- Startup command from docs or design.
- Test data.
- Expected prompts and outputs.

## Output

- Transcript exists.
- Exit code is recorded.
- Outputs and side effects are evaluated.
- Acceptance criteria are pass/fail.
