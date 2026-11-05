# SDLC Workflow Test

Execution supports Codex and Claude through the selected native host context.
See `SKILL.md` for invocation, required setup and evidence boundaries.

`sdlc-workflow-test` verifies the Agentic SDLC workflow from outside the
workflow. It checks the design contract, source-installed skill parity,
deterministic objective and execution capabilities, sequential fallback, Implement
interoperability, the managed outer-worktree lease lifecycle, hook
behavior, and optional private live-run evidence. Its opt-in live profile builds
and tests one real local three-tier application without changing the lightweight
default.

Hook-routing checks inspect command tokens after removing only the exact owned
agent-home prefix. A verification directory named with `sdlc` does not imply
phase routing; SDLC hook names and arguments are still rejected on prompt hooks.

The earlier skill-name change from `agentic-sdlc-test` to `sdlc-workflow-test`
was a hard ownership cut. Before that migration, destroy retained environments
with the old skill; the new skill does not adopt old-format roots or Docker
resources. Renaming the action to `--create-live-test` does not change lifecycle
ownership markers or require another migration.

Runtime registration now discovers owned resources and derives loopback endpoints
from inspection. Immutable named diagnostics survive automatic cleanup; worker
observations remain separate from clean build-bound acceptance. See
[runtime inventory](references/runtime-inventory.md) for the private path-only
interface, failure categories and classifier-linked development recovery.
Lifecycle v7 rejects incompatible active state; historical reports are preserved.
Git and native-hook consumers admit this retained state through the lifecycle
owner's existing 1 MiB limit. Small profile files remain bounded at 64 KiB;
oversized or unsafe files fail without discarding earlier diagnostics.

Historical original TDD evidence may survive appended corrective waves after
clean promotion. The verifier checks exact locked plans, completed owner replan
records, preserved completed tasks, the original TDD seal and unchanged execution
identity. Missing, altered or ambiguous lineage fails; later acceptance and new
phase submissions still require the current basis.

Completion acceptance clicks the initially unchecked control once, then waits
within the existing assertion deadline for its saved checked state. This supports
asynchronous confirmation and replacement elements while retaining independent
API/database and filter checks. The interaction helper is hash-bound in the
frozen bundle; previous bundles without it require a fresh trial.

The optional synthetic control regression uses the pinned Playwright dependency
and installed Chrome. In an isolated copy of `assets/headless-browser`, run
`npm ci --ignore-scripts --no-audit --no-fund`, then
`node node_modules/@playwright/test/cli.js test --config completion-control.config.mjs`.
It covers delayed confirmation, DOM replacement, wrong-row and never-confirmed
state, and an already-completed precondition. This is verifier regression proof,
separate from deterministic preflight and application acceptance.

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

Every live startup uses owned `up --force-recreate --detach --wait web db`.
Build web separately with owned `build web`. The helper rejects other startup
shapes before target resolution or Docker dispatch; it never silently adds
flags. Both containers are recreated while preserving the named database volume,
then the caller rediscovers the web endpoint and owned inventory. This prevents
stale service aliases and checkout metadata after network or configuration changes.
The restart owner refreshes the dynamic loopback endpoints before checking the
deployment and recording its receipt. A failed restart clears prior restart
proof; invalid ownership, publication or image identity cannot produce new proof.
Registration, deployment and restart use the single observed loopback binding,
including IPv4 or IPv6, independently of the application's internal port.
Literal endpoint addresses must match the observed address family.
Runtime registration and browser admission require the frozen `/api/v1/tasks`
API path without a query, with web, API and health URLs on the same owned
loopback origin. Endpoint mistakes fail before inventory changes or a browser
attempt; the health path may follow the application's declared health route.

The owned Compose actions are `build`, `exec`, `port`, `ps`, `restart`, `start`,
`stop`, and `up`. Worker teardown uses `stop web db` before releasing its lease;
final lifecycle cleanup deletes resources. Unsupported `down` and `logs` are
not alternate teardown or diagnostic interfaces. Ownership-checked, read-only
Docker observations may collect existing logs with sensitive output screened.
Retain named failing predicates and sanitized observation values before teardown;
an incomplete diagnostic remains an evidence gap, never a product diagnosis.
Docker documents that [Compose stop](https://docs.docker.com/reference/cli/docker/compose/stop/)
stops containers without removing them.

Worker runtime access keeps workflow imports available for the complete lease
check and Compose action, including deferred imports, and restores the prior
search path on success or failure. Fresh-process regression checks exercise real
lease validation without preloading execution modules or invoking Docker.

## What It Does

Phase-result input files use the exact private path
`<run-root>/evidence/phases/<phase>.json`. The recorder's `--evidence` help and
path-rejection diagnostic expose this requirement. Callers supply the canonical
file; the recorder alone creates immutable attempt archives. Use each installed
subcommand's help, including `--validation-command` for validation text.
`record-validation` is for canonical acceptance checks: a failed record cannot
be replaced, and an identical replay preserves its bytes and timestamp.
Pre-publication lint, expected TDD failures and worker development checks remain
in ordinary phase evidence and counted repair history. An already recorded
acceptance failure requires a fresh trial, even if it was recorded by mistake.

- Reads `docs/agentic-sdlc-design.md` as the workflow contract.
- Uses `references/verification-checklist.md` as the test plan.
- Runs `scripts/verify_agentic_sdlc.py` for static discovery, SDLC contract,
  capability regressions, a nested disposable project, and hook fixture checks.
  The worktree, execution, three-tier and task-wave aggregates use explicit
  900-second budgets; verifier and hook suites use 600 seconds, and other
  capability suites keep the 120-second default.
  Rejected fixtures are preserved, dependent hook checks are skipped with
  explicit prerequisite failures, and the verifier still writes its report.
  Failed suites report their exit code and bounded failing-test identifiers
  without copying raw diagnostic output.
  Stop fixtures require the coordinator-selected slice route and reject UAT
  inference from aggregate feature completion, including partially delivered
  features and future pending work.
  Execution excludes dispatch and task-wave contracts already run by their own
  suites. Missing required tests fail; skipped required tests remain partial.
  In-process fixtures crossing into commit admission clear inherited Git
  overrides locally; production environment-rejection guards remain enabled.
  Dedicated capability rows require full document coverage, exact source
  admission, phase/slice barriers, current completion evidence, 2,100-task
  graphs, indexed handoffs and the real-Git four-worktree retention limit.
  Source admission also requires actual first canonical publication from a
  committed baseline, exact contract-commit recovery, managed lease ordering,
  publication-race rejection and rejection of unjournaled commits. Three-tier
  composition fixtures invoke preparation for the contract commit and inspect
  its journal; fixture setup must not pre-satisfy that product transition.
  Lightweight profile evidence uses the current accepted-objective schema;
  the objective alone is insufficient for a live PASS.
- Compares configured hook payloads against their authoritative source: SDLC
  hooks come from `sdlc-start`, and shared runtime files come from
  `global-context-management`. Parity checks cover native wrapper registrations
  for both Codex and Claude.
  Both files must be regular and produce successful matching SHA-256 digests;
  missing or unreadable files never satisfy parity, even on both sides.
- Statically verifies the exact-SHA Agentic SDLC PR publication/review/merge
  modes and includes bounded observability, exact publication authorization,
  executed qualification, protected merge admission and recovery regressions.
  Hook denials alone cannot establish merge readiness.
- Requires installed `align`, `worktree`, `nebius-grafana-query`, and
  conditional `troubleshoot` support and includes them in source-installed
  parity and verification-identity checks. Parity also covers shared canonical
  specification, context, design/research, review/security/lint and PR delivery
  owners, including `maintain-project-specs` and the protected `merge-pr` broker.
  Missing shared owners fail even when both trees lack them.
  Project lifecycle and project-
  instruction evidence is advisory and absent from the golden path;
  `troubleshoot` is exercised only in controlled failure-routing scenarios.
- Verifies that `maintain-project-specs` remains the sole semantic, schema,
  template, validation, and receipt owner of canonical requirements and design
  while the two Agentic authoring phases remain routed adapters. Regression
  coverage includes pending draft-pair bootstrap, read-only requirements
  admission to design and full impact settlement before planning.
- Deterministically verifies normalized failure events, bounded diagnosis,
  authoritative repair control, positive design admission, and append-only
  corrective-plan/wave contracts. Troubleshooting admission checks both the
  skill's reference link and its canonical `references/workflow-handoffs.md`
  contract, so a missing handoff rule still fails after documentation refactors.
- Accepts a private `agentic-sdlc/verification-live-results-v3` manifest with
  `--live-evidence PATH`; see `assets/live-results.schema.json`.
- Rejects symlinked or unowned verification roots, unknown disposable
  directories, external or extra Git remotes, malformed or wrong-path hooks,
  synthetic no-change golden-path success, and any private or out-of-scope
  path touched anywhere in the supplied live history.
- Gives each fixture exactly one verifier-owned local bare `origin`, with a
  private generation/baseline receipt and an enforced push-rejection hook.
  Normal objective/worktree admission uses real local Git. External or additional
  remotes, redirects, borrowed objects, unsafe inherited command configuration
  and changed ownership fail before transport or cleanup. This does not verify
  hosted authentication, network failures, publication, PRs or merging.
- Current live profiles do not qualify native multi-slice document delivery or
  real remote merge. Deterministic program and merge regressions are reported
  separately and must not be presented as those live outcomes.
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
  at `sdlc-start` on the fixture baseline. This requires the coordinator's first
  phase checkpoint after intake;
  accepted registration alone is insufficient. The verifier never creates it.
  Exact JSON ownership and real hook discovery must agree before any other phase
  passes. Runtime targeting rejects
  empty or mismatched ownership; final semantic ingestion requires the receipt.
  Late recovery cannot certify an earlier trial: start a fresh trial.
- Aggregate collection requires all four ordered headless stage receipts,
  actual assertion reports, trace and screenshot hashes, exact target identity,
  closed browsers and the owned Compose restart. Failed attempts remain failed.
- Acceptance also requires an owned build receipt binding the clean checkout
  to the immutable running web image. Stale images, web mounts and endpoints
  outside that container cannot certify the selected revision.
- Newly admitted phase results require the current objective and basis.
  Failure classification binds the source checkout before preparation and the
  validated execution basis and integration checkout after preparation. A selected
  feature or locked plan alone is not prepared execution. Actual execution, basis
  or worktree intent requires strict target validation; partial or missing
  previously observed execution cannot fall back to a receipt without a basis.
  An immutable classifier receipt admitted before the first successful execution
  phase retains its original null basis after preparation. Validation still
  checks admission order, objective, artifact digest and Git ancestry; a new
  receipt after preparation requires the current execution basis.
  Historical admitted receipts survive only owner-linked, no-effect spec
  reconciliation with unchanged objective, plan, execution identities and Git
  ancestry. Missing lineage or material changes remain failures.
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
- Requires owner-executed test-class receipts bound to the promoted source,
  deployed image, observed result counters and artifact digest. Free-form logs
  cannot satisfy tests, and GUI counts derive from owned Playwright UAT
  receipts. See [test evidence](references/test-evidence.md).
- Derives deterministic and merge-safety profiles from passing verifier
  capabilities; live manifests cannot self-assert or relabel those profiles.
- Keeps lightweight PASS fail-closed until exact claims can be derived from
  underlying run artifacts. Three-tier PASS must byte-match
  the canonical result discovered from the profile source identity under the
  owned lifecycle, and pass the existing strict Git, layer, artifact,
  phase, ordered-GUI, correlation, and restart validator.

## Workflow

Shared SDLC/Implement workers have a one-hour per-attempt wall-clock deadline
with a five-minute warning, independent of heartbeat/stall guards. Harness waits
count, so service frozen runtime requests promptly and batch declared sequential
operations. Failed owned Compose commands retain a nonzero outcome with bounded
safe diagnostics; runtime credential values are redacted and sensitive output
is withheld. These diagnostics are not acceptance receipts.

1. Run safe static and hook preflight checks.
2. Review the generated report for blocking safety or discovery failures.
3. Use the SDLC phase skills on the nested disposable project for the seven
   required live lanes.
4. Store private lane evidence and a matching live-results manifest under the
   verification root, then rerun the verifier.
5. Treat any required FAIL as FAIL, missing live evidence as PARTIAL, and only
   complete deterministic plus live success as PASS.

## Files

Fresh lightweight and three-tier fixtures create a private Git profile, then
seal it to the owned origin receipt. The shared runtime validates exact profile
bytes, generation, repository/worktree membership and the executable origin
push guard. It excludes inherited configuration and global/system attributes;
local executable settings, inactive includes, caller overrides and tampering
remain failures. Unit fixture homes are also restored after each case, including
failed setup. Existing fixtures without a profile are not silently adopted.

Use `scripts/run_with_git_environment.py --profile <owned-profile> --cwd
<owned-checkout> -- <native-command>` to launch the native agent with the sealed
profile and a minimal environment. It requires the exact lifecycle-owned native
home, removes unrelated credentials and parent session identities, and redirects
shell initialization to that home without startup files. Codex configuration must
disable login shells and shell snapshots, enable default secret exclusions, and
pin `project_root_markers = [".git"]`. Project configuration is rejected only
from cwd through the validated checkout root, including linked worktree roots;
ordinary configuration above it remains separate. Bare origins cannot launch agents;
see [the launch settings](references/three-tier-process.md#native-launch-settings).
Worker processes inherit this environment; the private native authentication
store remains separate. This is environment isolation, not a separate operating
system identity or a restriction on all readable credential files. Never dump
environment values; inspect only necessary names or presence booleans.
This launcher does not advance SDLC phases and is not the application-runtime
launcher. Outer lifecycle operations use the shared validated Git environment
and their explicitly scoped private application environment. Use the lifecycle's
isolated native home for workflow state and current runtime/hook installation.
Do not edit personal Git settings, export
arbitrary overrides, allowlist filters or bypass a failed profile validation.

- `SKILL.md`: runtime verification workflow and safety boundaries.
- `references/verification-checklist.md`: detailed test plan and pass criteria.
- `scripts/verify_agentic_sdlc.py`: deterministic safe preflight verifier.
  A selected source catalog's synthetic hook fixture builder runs in its own
  bounded process, so an installed verifier retains one trusted runtime identity.
  Fixture setup failures produce sanitized FAIL results in the report.
- `scripts/collect_live_evidence.py`: private bounded artifact collector.
- `scripts/test_verify_agentic_sdlc.py`: verifier status and evidence tests.
- `scripts/test_git_environment.py`: profile isolation and tampering regressions.
- `scripts/run_with_git_environment.py`: sealed-profile native command launcher.
- `assets/live-results.schema.json`: private live-results manifest contract.
- `assets/three-tier-objective.md.template`: public-safe application
  objective body for the opt-in profile.
- `assets/three-tier-results.schema.json`: semantic live-profile result
  contract.
- `references/three-tier-live.md`: mode, phase, UAT, reporting, ownership, and
  cleanup contract.
- `scripts/three_tier_lifecycle.py`: private lifecycle/report/cleanup helper;
  it never orchestrates SDLC phases. Its fixed-image preparation uses an owned
  empty Docker CLI config only for bounded public pulls.
- `scripts/three_tier_browser.py`: fresh-profile Chrome launch, exact process
  identity validation, and process-group-scoped close helper.
- `scripts/render_three_tier_objective.py`: renders the canonical direct objective and five fixture placeholders.
- `scripts/test_three_tier_objective.py`: proves the rendered starter is accepted
  by public direct intake as a fresh objective revision.
- `scripts/three_tier_semantics.py`: focused semantic PASS validator shared by
  the lifecycle helper and its tests.
- `scripts/three_tier_reporting.py`: pure sanitized Markdown report renderer.
- `scripts/test_three_tier_lifecycle.py`: lifecycle ownership and cleanup tests.
- `scripts/test_three_tier_browser.py`: dedicated Chrome ownership tests.
- `agents/openai.yaml`: UI metadata and explicit-only invocation prompt.

The live harness resolves the active feature through the isolated host's validated
session/run binding, accepted objective revision and immutable execution basis.
Worker access to shared application resources requires the current assignment's
exclusive runtime lease. The worker acquires and holds it through the full live
sequence and teardown; the outer verifier consumes it without impersonating or
releasing worker ownership. A per-resource use lock guards synchronous actions
against lease release, while short state locks leave heartbeat/status available.
Before promotion, Compose and
execution-phase Git evidence use the exact registered integration worktree, with
matching project, run, feature, Git directory, branch and recorded HEAD. The target must be clean except while the root checkpoint explicitly records
active `sdlc-tdd`, `sdlc-update-documents` or `align`; those authoring phases may
run validation on uncommitted integration work before coordinator sealing.
Shipping and UAT always require a clean promoted checkout. After completed promotion,
Compose uses the exact promoted primary checkout and requires integration
resource cleanup. Caller-selected paths and unrecorded descendants are rejected.

Read-only classifier observations have a separate explicit target purpose. While
the bound root coordinator is running `sdlc-classify-failure`, it may observe
unsealed integration files without committing them or changing phase. Exact
worktree, objective, basis, branch, HEAD, ownership and ancestry checks remain.
This purpose is never used by Compose, runtime inventory, build certification,
browser acceptance or test execution; explicit clean-target requests still win.
Worker selectors and dirty promoted checkouts remain rejected.

Pre-commit runtime testing may select a worker only through the private helper's
`run-compose --worker-task TASK-NNN --assignment-digest DIGEST -- <action>`.
Both identifiers are required. The resolver admits only the assignment holding
the exclusive runtime lease in an active scheduler slot during `sdlc-implement-plan`, with a registered worktree,
unchanged assignment base, and an `ACTIVE` result from the execution owner's
live scope guard. Uncommitted changes must stay inside its write claims. This
selection never changes phase evidence or UAT targeting; after integration, rerun
the original oracle using the normal integration target. The shared runtime must
not serve concurrent worker tests.
