# Agentic SDLC Test

`agentic-sdlc-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks the design contract, source-installed skill parity,
deterministic prompt and execution capabilities, sequential fallback, Task
Implementer interoperability, the managed outer-worktree lease lifecycle, hook
behavior, and optional private live-run evidence. Its opt-in live profile builds
and tests one real local three-tier application without changing the lightweight
default.

## Invocation modes

```text
$agentic-sdlc-test
$agentic-sdlc-test --create
$agentic-sdlc-test --create --keep
$agentic-sdlc-test --destroy
```

- No flags run only the existing resource-validator preflight. It does not
  inspect or change Docker or a browser.
- `--create` runs deterministic preflight, creates a local task-board GUI,
  Django/Gunicorn server, and PostgreSQL database through the normal Agentic
  SDLC workflow, performs computer-use GUI UAT, writes a complete report, and
  destroys every exact owned live resource even after a test failure. If the
  shared Computer Use service becomes unhealthy and cannot safely close the
  dedicated tab, it instead fails closed as `CLEANUP_FAILED` and retains the
  owned runtime for separately authorized recovery.
- `--create --keep` retains the owned project, private evidence/state, running
  application, database volume, built image, and dedicated browser tab.
- `--destroy` removes the one retained owned application and raw evidence while
  preserving sanitized reports and lifecycle history. Repeating it returns
  `ALREADY_DESTROYED`.

`--keep` alone, mixed create/destroy, unknown lifecycle flags, and a second
create while an application is active are rejected before mutation. Existing
lightweight verifier options such as `--live-evidence` keep their current
meaning when no lifecycle action is selected.

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
- For explicit create modes, requires a semantic
  `agentic-sdlc/three-tier-results-v1` manifest, loopback-only dynamic web port,
  an internal-only database endpoint, exact labelled Docker ownership, five
  distinct recognized PNG/JPEG GUI checkpoints, unit/API/database/migration/
  vertical/GUI evidence, API/database correlation, and restart persistence.
- Treats initial Computer Use capture as capability discovery only. It repeats
  a just-in-time capture immediately before GUI evaluation and UAT, requires an
  unlocked host unless locked Computer Use is explicitly enabled for the
  session, plus a visible foreground current-Space browser window, reports
  pre-navigation visibility failures as `ENVIRONMENT_DEFECT`, and stops further
  Computer Use calls after a hang or shared-service response loss.

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
- `assets/three-tier-prompt.md.template`: managed public-safe application
  prompt body for the opt-in profile.
- `assets/three-tier-results.schema.json`: semantic live-profile result
  contract.
- `references/three-tier-live.md`: mode, phase, UAT, reporting, ownership, and
  cleanup contract.
- `scripts/three_tier_lifecycle.py`: private lifecycle/report/cleanup helper;
  it never orchestrates SDLC phases. Its fixed-image preparation uses an owned
  empty Docker CLI config only for bounded public pulls.
- `scripts/render_three_tier_prompt.py`: preserves the generated starter
  identity while rendering the canonical scenario body and five placeholders.
- `scripts/test_three_tier_prompt.py`: proves the rendered starter is accepted
  by the real prompt intake as a new `r0001` run.
- `scripts/three_tier_semantics.py`: focused semantic PASS validator shared by
  the lifecycle helper and its tests.
- `scripts/three_tier_reporting.py`: pure sanitized Markdown report renderer.
- `scripts/test_three_tier_lifecycle.py`: lifecycle ownership and cleanup tests.
- `agents/openai.yaml`: UI metadata and explicit-only invocation prompt.
