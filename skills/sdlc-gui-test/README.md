# GUI Test

`sdlc-gui-test` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Control, observe, and evaluate browser UI behavior with durable local evidence.
The evaluation plan may require human-like `computer-use`; Browser or
Playwright is not a silent substitute for that contract.

## Main Boundaries

- Do not use screenshots as the only interaction source when DOM or
  accessibility state is available.
- Refresh accessibility state after every action when using `computer-use`.
- Do not store secrets in screenshots or reports.
- Do not use production data without explicit permission.

## Primary Inputs

- GUI acceptance criteria.
- App URL or startup method.
- Test data or account instructions.
- Feature design.
- Required harness and browser, when constrained by the evaluation plan.

## Output

- Browser flow was executed.
- Evidence exists.
- Acceptance criteria are pass/fail.
- Screenshots or snapshots are stored locally.
