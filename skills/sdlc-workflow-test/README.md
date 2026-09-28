# SDLC Workflow Test

Execution supports Codex and Claude through the selected native host context.
See `SKILL.md` for invocation, required setup and evidence boundaries.

`sdlc-workflow-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks the design contract, source-installed skill parity,
deterministic prompt and execution capabilities, sequential fallback, Task
Implementer interoperability, the managed outer-worktree lease lifecycle, hook
behavior, and optional private live-run evidence. Its opt-in live profile builds
and tests one real local three-tier application without changing the lightweight
default.

The earlier skill-name change from `agentic-sdlc-test` to `sdlc-workflow-test`
was a hard ownership cut. Before that migration, destroy retained environments
with the old skill; the new skill does not adopt old-format roots or Docker
resources. Renaming the action to `--create-live-test` does not change lifecycle
ownership markers or require another migration.

## Invocation modes

```text
$sdlc-workflow-test
$sdlc-workflow-test --create-live-test
$sdlc-workflow-test --create-live-test --keep
$sdlc-workflow-test --resume
$sdlc-workflow-test --destroy
```

- No flags run only the existing lightweight deterministic verifier and its
  disposable fixture. It does not create, inspect, or change a real Docker
  application or browser session.
- `--create-live-test` runs deterministic preflight, safely destroys the previous active
  exactly owned test environment, creates a fresh local task-board GUI,
  Django/Gunicorn server, and PostgreSQL database through the normal Agentic
  SDLC workflow, performs headless Playwright Test GUI UAT, writes a complete report, and
  destroys every exact owned live resource even after a test failure. Each
  headless stage closes its exact owned processes and fails closed if process
  identity is ambiguous.
- `--create-live-test --keep` retains the owned project, private evidence/state,
  running application, database volume and built image; browsers still close.
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
`--live-evidence PATH` is the only evidence input and accepts the aggregate
manifest. Its three-tier profile identifies the owned canonical Docker/browser
results automatically. Aggregate and lifecycle identities and Git histories
are checked separately. Validate before cleanup; deleted canonical results
cannot be replaced by a copied source or report. Retired `--create` and
`--three-tier-results` are rejected without aliases or abbreviation.

The remote guard admits exactly one verifier-owned local bare `origin`; external,
extra, or mismatched remotes fail before fixture mutation.

When the selected checkout or dependency configuration changes, the live owner
recreates both containers while preserving the database volume, then rediscovers
the web endpoint. This keeps Compose file and dependency metadata aligned with
the selected source during restart checks.
The restart owner refreshes the dynamic loopback endpoints before checking the
deployment and recording its receipt. A failed restart clears prior restart
proof; invalid ownership, publication or image identity cannot produce new proof.

## What It Does

- Reads `docs/agentic-sdlc-design.md` as the workflow contract.
- Uses `references/verification-checklist.md` as the test plan.
- Runs `scripts/verify_agentic_sdlc.py` for static discovery, SDLC contract,
  capability regressions, a nested disposable project, and hook fixture checks.
  The two measured slow aggregates use explicit bounded budgets: 300 seconds
  for the worktree matrix and 900 seconds for the Task Implementer wave matrix;
  every other capability suite keeps the 120-second default.
- Compares configured hook payloads against their authoritative source: SDLC
  hooks come from `sdlc-start`, and shared runtime files come from
  `global-context-management`. Parity checks cover native wrapper registrations
  for both Codex and Claude.
  Both files must be regular and produce successful matching SHA-256 digests;
  missing or unreadable files never satisfy parity, even on both sides.
- Statically verifies the exact-SHA Agentic SDLC PR publication/review/merge
  modes and includes bounded observability plus explicit-PR, canonical
  single-action publication and merge authorization in capability regressions.
- Requires installed `align`, `worktree`, `nebius-grafana-query`, and
  conditional `troubleshoot` support and includes them in source-installed
  parity and verification-identity checks. Project lifecycle and project-
  instruction evidence is advisory and absent from the golden path;
  `troubleshoot` is exercised only in controlled failure-routing scenarios.
- Verifies that `maintain-project-specs` remains the sole semantic, schema,
  template, validation, and receipt owner of canonical requirements and design
  while the two Agentic authoring phases remain routed adapters. Regression
  coverage includes pending draft-pair bootstrap, read-only requirements
  admission to design and full impact settlement before planning.
- Deterministically verifies normalized failure events, bounded diagnosis,
  authoritative repair control, positive design admission, and append-only
  corrective-plan/wave contracts.
- Accepts a private `agentic-sdlc/verification-live-results-v3` manifest with
  `--live-evidence PATH`; see `assets/live-results.schema.json`.
- Rejects symlinked or unowned verification roots, unknown disposable
  directories, external or extra Git remotes, malformed or wrong-path hooks,
  synthetic no-change golden-path success, and any private or out-of-scope
  path touched anywhere in the supplied live history.
- Gives each fixture exactly one verifier-owned local bare `origin`, with a
  private generation/baseline receipt and an enforced push-rejection hook.
  Normal prompt/worktree admission uses real local Git. External or additional
  remotes, redirects, borrowed objects, unsafe inherited command configuration
  and changed ownership fail before transport or cleanup. This does not verify
  hosted authentication, network failures, publication, PRs or merging.
- Writes the verification report to `<agent-home>/sdlc-verification/report.md`.
- Keeps real repositories, installed skills, hooks, and agent configuration
  unchanged.
- For explicit create modes, requires a semantic
  `agentic-sdlc/three-tier-results-v3` manifest, loopback-only dynamic web port,
  an internal-only database endpoint, exact labelled Docker ownership, five
  independently captured PNG/JPEG GUI checkpoints, unit/API/database/migration/
  vertical/GUI evidence, API/database correlation, and restart persistence.
- Rediscovers the Docker-assigned web port after restart and refreshes recorded
  endpoints while proving the original database volume remains in use.
- Records a digest-bound startup receipt only while the coordinator is still
  at `sdlc-start` on the fixture baseline. Exact JSON ownership and real hook
  discovery must agree before any other phase passes. Runtime targeting rejects
  empty or mismatched ownership; final semantic ingestion requires the receipt.
  Late recovery cannot certify an earlier trial: start a fresh trial.
- Aggregate collection requires all four ordered headless stage receipts,
  actual assertion reports, trace and screenshot hashes, exact target identity,
  closed browsers and the owned Compose restart. Failed attempts remain failed.
- Acceptance also requires an owned build receipt binding the clean checkout
  to the immutable running web image. Stale images, web mounts and endpoints
  outside that container cannot certify the selected revision.
- The pinned verifier Playwright Test bundle may download npm dependencies at
  bootstrap. All application and browser execution remains local.
- Headless web acceptance has no native desktop capture, foreground window,
  monitor or unlocked-screen prerequisite. See
  [the process contract](references/three-tier-process.md) for stage isolation,
  reset ownership, retained-runtime behavior and locked-agent certification.
- Reports every required SDLC skill with deterministic, lightweight,
  three-tier, or safety evidence. Every semantic assertion is identity-bound
  and backed by a private owner-local artifact plus SHA-256 digest; labels or
  booleans without provenance are rejected.
- Provides `scripts/collect_live_evidence.py` as the fail-closed path for
  copying and hashing bounded profile, lane, and skill artifacts. It never
  overwrites different evidence bytes. Its skill owners match the complete
  required evidence matrix, including `align`; support-only skills remain
  excluded. Collector regressions run in the deterministic preflight.
- Checks current operational clauses in the canonical design and phase skills,
  ignoring Markdown whitespace wrapping. Negative controls still reject
  missing boundary, evaluation, alignment, and lifecycle-independence duties.
- Treats generic digest-backed artifacts and minimally shaped profile headers
  as PARTIAL only. PASS requires dedicated machine-semantic validation of both
  the assertion and its canonical source profile.
- Derives deterministic and merge-safety profiles from passing verifier
  capabilities; live manifests cannot self-assert or relabel those profiles.
- Keeps lightweight PASS fail-closed until exact claims can be derived from
  underlying run artifacts. Three-tier PASS must byte-match
  the canonical result discovered from the profile source identity under the
  owned lifecycle, and pass the existing strict Git, layer, artifact,
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

The native workflow CI regression step sets `GIT_CONFIG_NOSYSTEM=1` so
disposable Git fixtures do not inherit runner-installed system filters such as
Git LFS. Repository and user configuration checks remain active, and the step
runs `scripts/test_owned_git_origin.py` to verify the ownership boundary.

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

The live harness resolves the active feature through the isolated host's validated
prompt workspace and canonical execution status. Before promotion, Compose and
execution-phase Git evidence use the exact registered integration worktree, with
matching project, run, feature, Git directory, branch and recorded HEAD. The target must be clean except while the root checkpoint explicitly records
active `sdlc-tdd`, `sdlc-update-documents` or `align`; those authoring phases may
run validation on uncommitted integration work before coordinator sealing.
Shipping and UAT always require a clean promoted checkout. After completed promotion,
Compose uses the exact promoted primary checkout and requires integration
resource cleanup. Caller-selected paths and unrecorded descendants are rejected.

Pre-commit runtime testing may select a worker only through the private helper's
`run-compose --worker-task TASK-NNN --assignment-digest DIGEST -- <action>`.
Both identifiers are required. The resolver admits only the sole task in the
active capacity batch during `sdlc-implement-plan`, with a registered worktree,
unchanged assignment base, and an `ACTIVE` result from the execution owner's
live scope guard. Uncommitted changes must stay inside its write claims. This
selection never changes phase evidence or UAT targeting; after integration, rerun
the original oracle using the normal integration target. The shared runtime must
not serve concurrent worker tests.
