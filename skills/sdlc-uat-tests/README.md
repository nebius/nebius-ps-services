# UAT Tests

`sdlc-uat-tests` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Validate the full product, not just individual features, before PR creation,
using the requirements Live Experiment Environment only when it is confirmed
safe and allowed.
Web GUI rows default to headless Playwright Test with isolated processes and
contexts, actual browser identity, target SHA, assertions, traces, screenshots
and cleanup receipts. Optional headless MCP exploration is separate. A locked
screen does not introduce a native capture prerequisite. Explicit native-desktop
criteria retain their separately declared harness.

## Main Boundaries

- Do not create PRs or merge.
- Do not ignore failed negative criteria.
- Do not modify requirements or design directly.
- Do not use production or unconfirmed environments for live experiments.
- Do not substitute Browser or Playwright for required computer-use evidence.

## Primary Inputs

- All requirements.
- All feature designs.
- All feature evidence.
- Current branch.
- Product startup instructions.
- Live Experiment Environment section from `docs/requirements.md`, when present.

## Output

- Full UAT report exists.
- All P0 acceptance criteria pass or blockers are classified.
- Cross-feature flows pass.
- Product is ready for publication-only `create-pr` only on pass and only for
  the clean exact promoted SHA tested by UAT.
