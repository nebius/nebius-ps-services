# Agentic SDLC Test

`agentic-sdlc-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks the design contract, source-installed skill parity,
deterministic prompt and execution capabilities, sequential fallback, Task
Implementer interoperability, the managed outer-worktree lease lifecycle, hook
behavior, and optional private live-run evidence.

## What It Does

- Reads `docs/agentic-sdlc-design.md` as the workflow contract.
- Uses `references/verification-checklist.md` as the test plan.
- Runs `scripts/verify_agentic_sdlc.py` for static discovery, SDLC contract,
  capability regressions, a nested disposable project, and hook fixture checks.
- Accepts a private `agentic-sdlc/verification-live-results-v1` manifest with
  `--live-evidence PATH`; see `assets/live-results.schema.json`.
- Rejects symlinked or unowned verification roots, unknown disposable
  directories, remote-backed repositories, malformed or wrong-path hooks,
  synthetic no-change golden-path success, and any private or out-of-scope
  path touched anywhere in the supplied live history.
- Writes the verification report to `~/.codex/sdlc-verification/report.md`.
- Keeps real repositories, installed skills, hooks, and agent configuration
  unchanged.

## Workflow

1. Run safe static and hook preflight checks.
2. Review the generated report for blocking safety or discovery failures.
3. Use the SDLC phase skills on the nested disposable project for the seven
   required live lanes.
4. Store private lane evidence and a matching live-results manifest under the
   verification root, then rerun the verifier.
5. Treat any required FAIL as FAIL, missing live evidence as PARTIAL, and only
   complete deterministic plus live success as PASS.

## Files

- `SKILL.md`: runtime verification workflow and safety boundaries.
- `references/verification-checklist.md`: detailed test plan and pass criteria.
- `scripts/verify_agentic_sdlc.py`: deterministic safe preflight verifier.
- `scripts/test_verify_agentic_sdlc.py`: verifier status and evidence tests.
- `assets/live-results.schema.json`: private live-results manifest contract.
- `agents/openai.yaml`: UI metadata and explicit-only invocation prompt.
