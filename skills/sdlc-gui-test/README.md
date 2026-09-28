# GUI Test

`sdlc-gui-test` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Control, observe, and evaluate browser UI behavior with durable local evidence.
Web acceptance defaults to headless Playwright Test with fresh owned processes
and contexts. Optional headless isolated Playwright MCP exploration is separate.
Native desktop capture, foreground windows and screen unlock are not web test
prerequisites; explicitly declared native-desktop contracts remain separate.

## Main Boundaries

- Do not use screenshots as the only interaction source when DOM or
  accessibility state is available.
- Use explicit assertions, traces and screenshots; close browsers even when
  keeping the application. Fresh MCP snapshots guide optional exploration.
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
