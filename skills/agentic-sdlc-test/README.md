# Agentic SDLC Test

`agentic-sdlc-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks the design contract, global `sdlc-*` skill discovery, hook
configuration, disposable hook behavior, idempotency expectations, failure-loop
routing, steering, and the disposable golden-path workflow.

## What It Does

- Reads `docs/agentic-sdlc-design.md` as the workflow contract.
- Uses `references/verification-checklist.md` as the test plan.
- Runs `scripts/verify_agentic_sdlc.py` for static discovery and disposable
  hook fixture checks.
- Writes the verification report to `~/.codex/sdlc-verification/report.md`.
- Keeps real repositories, installed skills, hooks, and agent configuration
  unchanged.

## Workflow

1. Run safe static and hook preflight checks.
2. Review the generated report for blocking safety or discovery failures.
3. Use the SDLC phase skills on the disposable project for the golden path.
4. Verify rerun, change-request, failure-loop, steering, continuation, GUI, and
   TUI behavior.
5. Update the report with PASS, PARTIAL, or FAIL.

## Files

- `SKILL.md`: runtime verification workflow and safety boundaries.
- `references/verification-checklist.md`: detailed test plan and pass criteria.
- `scripts/verify_agentic_sdlc.py`: deterministic safe preflight verifier.
- `agents/openai.yaml`: UI metadata and invocation prompt.
