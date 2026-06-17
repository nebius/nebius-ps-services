# UAT Tests

`sdlc-uat-tests` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Validate the full product, not just individual features, before PR creation.

## Main Boundaries

- Create PRs.
- Merge.
- Ignore failed negative criteria.
- Modify requirements or design directly.

## Primary Inputs

- All requirements.
- All feature designs.
- All feature evidence.
- Current branch.
- Product startup instructions.

## Output

- Full UAT report exists.
- All P0 acceptance criteria pass or blockers are classified.
- Cross-feature flows pass.
- Product is ready for `create-pr` only on pass.
