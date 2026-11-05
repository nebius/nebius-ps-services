# UAT Tests

`sdlc-uat-tests` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Qualify each promoted delivery slice and its inherited product regression
obligations before PR creation. Use a confirmed safe, authorized live target
when applicable; accepted non-runtime changes use reviewed live N/A evidence.
Future slices stay pending, and slice success alone does not complete a feature
or phase.
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

- The frozen slice criteria, inherited regression obligations and source mappings.
- The canonical requirements and designs relevant to those obligations.
- The current slice and dependency evidence.
- Current branch.
- Product startup instructions.
- Live Experiment Environment section from `docs/requirements.md`, when present.

## Output

- A complete UAT report for the current slice exists.
- Every mandatory slice and inherited regression criterion passes with executed evidence; missing or failed evidence blocks delivery.
- Cross-feature flows pass.
- Product is ready for publication-only `create-pr` only on pass and only for
  the clean exact promoted SHA tested by UAT.
