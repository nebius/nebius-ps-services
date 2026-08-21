# SDLC Workflow Test

`sdlc-workflow-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks the design contract, source-installed skill parity,
deterministic prompt and execution capabilities, sequential fallback, Task
Implementer interoperability, the managed outer-worktree lease lifecycle, hook
behavior, and optional private live-run evidence. Its opt-in live profile builds
and tests one real local three-tier application without changing the lightweight
default.

This rename is a hard ownership cut. Before installing it, destroy any retained
live verifier environment with the currently installed pre-rename skill. The
renamed skill intentionally does not read, migrate, resume, or destroy roots
and Docker resources that carry old-format markers, labels, or Compose names.

## Invocation modes

```text
$sdlc-workflow-test
$sdlc-workflow-test --create
$sdlc-workflow-test --create --keep
$sdlc-workflow-test --resume
$sdlc-workflow-test --destroy
```

- No flags run only the existing lightweight deterministic verifier and its
  disposable fixture. It does not create, inspect, or change a real Docker
  application or browser session.
- `--create` runs deterministic preflight, safely destroys the previous active
  exactly owned test environment, creates a fresh local task-board GUI,
  Django/Gunicorn server, and PostgreSQL database through the normal Agentic
  SDLC workflow, performs computer-use GUI UAT, writes a complete report, and
  destroys every exact owned live resource even after a test failure. If the
  shared Computer Use service cannot prove the dedicated marker, it makes no
  browser action. Cleanup closes only the exact verifier-owned Chrome process
  group and fails closed on any process-identity ambiguity.
- `--create --keep` performs the same replacement first, then retains the new
  owned project, private evidence/state, running application, database volume,
  built image, and dedicated verifier-owned Chrome instance/profile.
- `--resume` revalidates and continues one retained failed or partial run.
- `--destroy` removes the one retained owned application and raw evidence while
  preserving sanitized reports and lifecycle history. It closes only the exact
  recorded verifier-owned Chrome process group; existing Chrome instances are
  never targets. Repeating it returns `ALREADY_DESTROYED`.

`--keep` alone, mixed create/destroy, and unknown lifecycle flags are rejected
before mutation. A repeated create never starts a second live stack: it uses
the standalone destroy path to remove the previous owned environment first.
It also discovers exact dual-labelled resources created before inventory was
persisted, canonicalizes name/ID aliases before removal, and preserves a
cumulative cleanup ledger across retries. Mutating helper and Compose actions are fenced by the immutable
verification ID, so a superseded invocation cannot continue against the new
active lifecycle. If exact ownership or cleanup cannot be proven, replacement
stops fail-closed.
Existing lightweight verifier options such as `--live-evidence` keep their
current meaning when no lifecycle action is selected.

## What It Does

- Reads `docs/agentic-sdlc-design.md` as the workflow contract.
- Uses `references/verification-checklist.md` as the test plan.
- Runs `scripts/verify_agentic_sdlc.py` for static discovery, SDLC contract,
  capability regressions, a nested disposable project, and hook fixture checks.
  The two measured slow aggregates use explicit bounded budgets: 300 seconds
  for the worktree matrix and 900 seconds for the Task Implementer wave matrix;
  every other capability suite keeps the 120-second default.
- Statically verifies the exact-SHA Agentic SDLC PR publication/review/merge
  modes and includes bounded observability plus explicit-PR, canonical
  single-action publication and merge authorization in capability regressions.
- Requires installed `maintain-project-specs`, `worktree`,
  `nebius-grafana-query`, `project-agent-instructions`, and conditional
  `troubleshoot` support and includes them in source-installed parity and
  verification-identity checks.
  `project-agent-instructions` is a golden-path step after design;
  `troubleshoot` is exercised only in controlled failure-routing scenarios and
  remains absent from the golden path.
- Verifies that `maintain-project-specs` remains the sole semantic, schema,
  template, validation, and receipt owner of canonical requirements and design
  while the two Agentic authoring phases remain routed adapters.
- Deterministically verifies normalized failure events, bounded diagnosis,
  authoritative repair control, positive design admission, and append-only
  corrective-plan/wave contracts.
- Accepts a private `agentic-sdlc/verification-live-results-v3` manifest with
  `--live-evidence PATH`; see `assets/live-results.schema.json`.
- Rejects symlinked or unowned verification roots, unknown disposable
  directories, remote-backed repositories, malformed or wrong-path hooks,
  synthetic no-change golden-path success, and any private or out-of-scope
  path touched anywhere in the supplied live history.
- Writes the verification report to `~/.codex/sdlc-verification/report.md`.
- Keeps real repositories, installed skills, hooks, and agent configuration
  unchanged.
- For explicit create modes, requires a semantic
  `agentic-sdlc/three-tier-results-v2` manifest, loopback-only dynamic web port,
  an internal-only database endpoint, exact labelled Docker ownership, five
  distinct recognized PNG/JPEG GUI checkpoints, unit/API/database/migration/
  vertical/GUI evidence, API/database correlation, and restart persistence.
- Treats initial Computer Use capture as capability discovery only. It repeats
  a just-in-time capture immediately before GUI evaluation and UAT, requires an
  unlocked host unless locked Computer Use is explicitly enabled for the
  session, plus a visible foreground current-Space browser window, reports
  pre-navigation visibility failures as `ENVIRONMENT_DEFECT`, and stops further
  Computer Use calls after a hang or shared-service response loss. Every
  Computer Use action requires the exact verification-ID marker from the fresh
  verifier-owned Chrome profile.
- Reports every required SDLC skill with deterministic, lightweight,
  three-tier, or safety evidence. Every semantic assertion is identity-bound
  and backed by a private owner-local artifact plus SHA-256 digest; labels or
  booleans without provenance are rejected.
- Provides `scripts/collect_live_evidence.py` as the fail-closed path for
  copying and hashing bounded profile, lane, and skill artifacts. It never
  overwrites different evidence bytes.
- Treats generic digest-backed artifacts and minimally shaped profile headers
  as PARTIAL only. PASS requires dedicated machine-semantic validation of both
  the assertion and its canonical source profile.
- Derives deterministic and merge-safety profiles from passing verifier
  capabilities; live manifests cannot self-assert or relabel those profiles.
- Keeps lightweight PASS fail-closed until exact claims can be derived from
  underlying run artifacts. Three-tier PASS must byte-match
  `--three-tier-results` and pass the existing strict Git, layer, artifact,
  phase, ordered-GUI, correlation, and restart validator.

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
- `scripts/collect_live_evidence.py`: private bounded artifact collector.
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
- `scripts/three_tier_browser.py`: fresh-profile Chrome launch, exact process
  identity validation, and process-group-scoped close helper.
- `scripts/render_three_tier_prompt.py`: preserves the generated starter
  identity while rendering the canonical scenario body and five placeholders.
- `scripts/test_three_tier_prompt.py`: proves the rendered starter is accepted
  by the real prompt intake as a new `r0001` run.
- `scripts/three_tier_semantics.py`: focused semantic PASS validator shared by
  the lifecycle helper and its tests.
- `scripts/three_tier_reporting.py`: pure sanitized Markdown report renderer.
- `scripts/test_three_tier_lifecycle.py`: lifecycle ownership and cleanup tests.
- `scripts/test_three_tier_browser.py`: dedicated Chrome ownership tests.
- `agents/openai.yaml`: UI metadata and explicit-only invocation prompt.
