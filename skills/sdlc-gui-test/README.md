# GUI Test

`sdlc-gui-test` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Control, observe, and evaluate browser UI behavior with durable local evidence.

## Main Boundaries

- Use screenshots as the only interaction source when DOM or accessibility snapshots are available.
- Store secrets in screenshots or reports.
- Use production data without explicit permission.

## Primary Inputs

- GUI acceptance criteria.
- App URL or startup method.
- Test data or account instructions.
- Feature design.

## Output

- Browser flow was executed.
- Evidence exists.
- Acceptance criteria are pass/fail.
- Screenshots or snapshots are stored locally.
