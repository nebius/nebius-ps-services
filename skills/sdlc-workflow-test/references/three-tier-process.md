# Three-Tier Live Process

Use this process only after explicit `--create-live-test`, `--create-live-test --keep`, or
`--resume`:

Use the [phased qualification contract](phased-live-qualification.md) for every
generation. Preserve supplied milestones or derive them from the frozen source;
observe slice completion, native continuation and accepted future-plan changes.

1. Read `references/three-tier-live.md`. Confirm Docker, Compose, Git, Node.js
   20+, npm, installed Chrome and source-installed skill parity. Preparation
   freezes the independent Playwright Test bundle; `run-browser-stage --stage
   capability-discovery` proves actual headless interaction and screenshot
   capture. Missing capability before an attempt is PARTIAL; an attempted
   required stage failure is FAIL.
   Native three-tier qualification currently supports Codex only. Claude's
   disposable-home catalog binding and native event adapter remain unqualified;
   report that capability as PARTIAL before preparing a Claude live trial.
   Check `codex --version` against the native adapter's documented exact version
   before preparation. An unsupported version is a capability gap; do not start
   a trial or relax evidence checks to hide it.
2. After aggregate cleanup and fresh deterministic preflight, select the native
   host's installed catalog through the existing host installation convention.
   Run its absolute `sdlc-workflow-test/scripts/three_tier_lifecycle.py` entrypoint
   in a fresh Python process for preparation and every later lifecycle action.
   Use that same catalog for the launcher and aggregate workspace cleanup.
   Preparation freezes its physical root and runtime closure; source paths with
   identical bytes still differ from the installed owner's execution basis.
   Never choose executable paths from a lifecycle receipt, replace cached owner
   modules, or normalize basis paths. Catalog relocation or drift fails closed.
   The source verifier uses its configured installed root for a separate read-only
   `validate-results` process when ingesting the live profile. Its `prepare` action serializes lifecycle
   changes, destroys the previous active environment through the same exact
   ownership-checked cleanup path as standalone destroy, and only then creates
   a fresh verification ID and project. If ownership, project safety, or
   cleanup cannot be proven, stop without creating a replacement. Cleanup must
   canonicalize and deduplicate every recorded/discovered Docker alias by
   resource identity after validating both exact ownership labels, covering a
   prior run interrupted before inventory capture. Its cumulative ledger must
   survive failed retries, and an already-absent resource counts as success only
   when a fresh inspect proves absence. Retain the returned verification ID as
   this invocation's immutable
   generation fence. Pass it as `--expected-verification-id` to every later
   mutating helper action; never refresh it from a newer status response. Run
   the helper's `prepare-images` action to pull the fixed public base images
   through an owned empty Docker CLI config and build one labelled, immutable
   dependency image for task acceptance. Do not reuse that config for Compose.
   Task trials use fresh containers and remove them immediately. For resume mode, require the existing lifecycle to
   be owned, KEPT, and previously FAIL or PARTIAL; revalidate its project
   boundary and recorded resources without creating replacements. Use the
   private root's isolated agent home for all accepted objective and phase state;
   never reuse or delete the user's ordinary Agentic SDLC run directory.
   Preparation also creates and seals `<run-root>/.sdlc-git-environment/context.json`.
   Launch native coordinator commands through `scripts/run_with_git_environment.py`
   with that profile and the owned checkout, after applying the native launch
   settings below. The runtime propagates the same
   validated context to workers; every Git owner rechecks scope and integrity.
   Keep current runtime/hook installation inside the selected disposable native
   home. Use the existing installer and normal native hook review during setup,
   before freezing the trial; never copy trust state or use bypass flags. Do not overwrite ordinary hooks or substitute manual Git overrides.
3. Establish the outer verifier as the physical-host memory owner before either
   native session starts: obtain a fresh reliable observation, keep a bounded
   monitor active, consume its results throughout both sessions and live runtime,
   and cancel exact owned work at the existing critical/unknown boundary. Record
   process ownership at launch and verify its workspace/lifecycle before stopping
   it. If visibility or monitoring is unavailable, report the capability gap
   before launch. The request's environment handoff is setup context, not proof
   of monitoring; repeat it in worker assignments. A sandboxed child may rely on
   this actual outer owner without duplicating an unavailable host probe.
   Render the disposable request with `scripts/render_three_tier_objective.py`
   and invoke `$sdlc-start <rendered fixture objective>` from the owned project.
   Resume another native session with `$sdlc-start continue <project-folder>`.
   Follow the returned phase skill; the harness and hooks never perform private
   product transitions to satisfy workflow acceptance.
   Preserve the objective's exact `not-a-secret` placeholder in offline
   credential mocks, patched environments and expected-call assertions, and
   repeat that constraint in affected worker assignments. Actual runtime
   credentials use the protected environment file. Keep sensitive-content
   screening active and retain rejected attempts under normal recovery rules.
   Complete the coordinator's first-start initialization after intake and before
   reading or recording its startup checkpoint. Accepted registration alone does
   not create phase state; follow `sdlc-start/references/state-schema.md`. The
   native coordinator invokes `sdlc-start/scripts/initialize_phase.py`; existing
   hook telemetry is not prior phase completion. The verifier observes that
   transition and must not invoke initialization on the coordinator's behalf.
   Write the observation JSON at the exact private path
   `<run-root>/evidence/phases/<phase>.json`, including
   `evidence/phases/sdlc-start.json` for startup, and supply it with
   `record-phase --evidence`. Read the installed subcommand help before calling
   it; immutable attempt archives belong to the recorder alone. Validation
   records use `--validation-command`, not an inferred flag name.
   For initial `sdlc-start` PASS, the native coordinator supplies its actual
   session identity as `--coordinator-session` in the outer observation request.
   The recorder validates that selector against the owned binding and passes it
   explicitly to the source loader; never overwrite the outer process identity.
   Later observations use the startup receipt and reject an unused selector.
   Record only phases admitted by the installed recorder. Conditional
   `troubleshoot` returns `diagnosis-v3` to `sdlc-classify-failure`; preserve its
   diagnostic artifacts without inventing a `troubleshoot` phase receipt.
   Validation records represent canonical acceptance checks; any recorded FAIL requires a
   fresh trial. Keep repairable authoring and development checks in ordinary
   phase evidence and counted repair history, preserving every failed attempt.
   Before dispatching that phase, record the startup phase while its checkpoint
   still names `sdlc-start`, before plans or execution state exist and before
   Git leaves the fixture baseline. The lifecycle observes exact JSON ownership
   and source state-loader discovery and retains its digest-bound startup v3 receipt. It
   rejects later phase PASS and final PASS without this receipt. After natural
   native tool/hook events exist, run generation-fenced `record-native-activation`;
   final PASS additionally requires its independent native correlation receipt. A late repair
   starts a fresh trial and does not rehabilitate earlier evidence.
   For classifier, TDD or preparation PASS, the private adapter obtains the HEAD
   with `execution_target(state, require_clean=False, phase_observation=phase)`
   where phase is `sdlc-classify-failure`, `sdlc-tdd` or `sdlc-prepare-execution`.
   The lifecycle consumes the same purpose for identity and receipt validation.
   Preparation remains clean and must prove the completed owner replan before
   observing a corrective boundary. Subsequent TDD observation of that completed
   replan reuses its exact authority and quiescence proofs while execution remains
   `repair_required`; it requires the clean failed commit and existing sealed tests.
   It does not permit test authoring. Initial or typed test-revision TDD output may remain
   unsealed at `tests_red` or `tests_ready`; never seal or change phase merely to
   satisfy observation. Await the receipt before dependent actions. Runtime and
   acceptance never use this purpose or substitute the clean primary checkout.
   When classification observes a failed integrated wave whose counted parent is
   suspended, use the repair owner's read-only lineage proof. The exact pending
   parent must connect to the current counted child through immutable suspension
   records with the same commit and preserved obligations and budgets. Observation
   never resumes that parent or admits corrective execution or runtime access.
4. Build all three logical layers: browser GUI, Django/Gunicorn web/API server,
   and PostgreSQL. Run exactly two labelled Compose containers, dynamically
   publish only the web port on loopback, and keep PostgreSQL private to the
   Compose network. Use a project-owned normal bridge, without `internal: true`,
   so Docker can publish the loopback web port. No database port is published.
   Run every Compose action through the helper's
   generation-locked `run-compose` action with the immutable expected
   verification ID; never invoke a mutating `docker compose` command directly.
   This makes a replacement wait for an in-flight action and prevents a
   superseded invocation from starting or changing a stack. Collect inventory with private `record-runtime --web-path / --health-path /healthz`
   using the declared routes and, for a worker, its paired assignment selectors.
   Follow [runtime inventory](runtime-inventory.md); the verifier discovers
   resource identities and derives endpoints from inspected relationships.
   Use owned `build web`, then
   `up --force-recreate --detach --wait web db` for every startup.
   The helper requires both services and all three flags exactly once;
   their order after `up` may vary. It rejects all other startup forms before
   resolving a target or invoking Docker. There is no combined `up --build`
   path and no automatic flag insertion. Recreation retains the named database
   volume and prevents reused containers from losing service aliases when a
   network is replaced or retaining metadata from an earlier checkout.
   Preserve any failed startup; this rule does not authorize bypassing a
   blocked task, classifier or lease. Docker documents forced recreation
   and volume preservation in its
   [Compose up reference](https://docs.docker.com/reference/cli/docker/compose/up/).
   Repeat owned inventory collection after every recreation and restart.
   The observer derives the dynamic IPv4 or IPv6 loopback origin from the
   inspected publication. Preserve every failed observation before cleanup.
5. Execute every phase and test class in the scenario reference. Local ship
   means build and run the promoted clean SHA locally; it never means push,
   publish, PR creation, or PR merge.
   Before UAT fixture reset, collect all five non-GUI classes through the
   generation-locked `run-test-class` owner described in
   [test evidence](test-evidence.md). Reuse owned Playwright UAT receipts for
   GUI evidence. Generic logs or hand-written PASS/counts cannot qualify tests.
   Service frozen worker runtime requests promptly, batching their declared
   sequential operations without unrelated exploration between them. The shared
   worker has no healthy-work expiry while waiting. Return actual failures
   promptly to the coordinator; keep unknown outcomes reserved until reconciled.
   Failed owned Compose commands include a bounded sanitized diagnostic tail and
   their command exit code. Known private runtime values are redacted before
   truncation; credential-like output is withheld. Preserve the nonzero outcome.
   Do not rerun a failed test merely to recover output already provided, and do
   not treat diagnostics as canonical test receipts. Healthy Compose
   actions continue through periodic observation checkpoints without expiry.
   Before worker lease release, request owned `stop web db` for teardown;
   resource deletion remains with final lifecycle cleanup. Do not substitute
   unsupported `down` or `logs` actions. See the live reference for the action
   list and ownership-checked read-only diagnostic path.
   An outer observation must retain its named failed predicate, sanitized
   expected/observed values and source location before teardown changes the
   observed state. If that evidence is unavailable, preserve the failed outcome
   and report the gap; later observations cannot reconstruct earlier state or
   establish a product defect. Diagnostic collection grants no retry authority.
6. Route web evaluation and UAT through `sdlc-gui-test` with
   `harness: playwright-test` and `headless: true`. Use the owned stage command
   at the matching phase; see the headless contract below.
   Build the clean selected target through owned `build web`
   before recording runtime inventory. The owner binds its immutable image ID
   to that checkout revision and rechecks image, mounts and endpoint ownership
   at browser launch and every independent checkpoint.
   Register the objective's exact `/api/v1/tasks` API path without a query.
   Web, API and health endpoints must share the discovered loopback origin;
   derive paths from the frozen contract and declared application routes.
   Completion acceptance requires an initially unchecked control, one click and
   a bounded auto-retrying assertion of saved checked state. Server confirmation
   may be asynchronous and rendering may replace the element. Keep completed
   API/database and filter checks; never force state, repeat the action, insert
   oracle sleeps or extend deadlines. The shared control helper is frozen and
   hash-checked with the oracle. Older bundles without it require a fresh trial.
7. Preserve failed receipts, traces and reports. Recovery cannot relabel a
   failed trial. Browser cleanup validates the exact owned process identity;
   ambiguity is `CLEANUP_FAILED`.
8. Persist semantic results incrementally using
   `agentic-sdlc/three-tier-results-v5`. Failed runs retain validated partial
   layer/test status; the lifecycle helper derives PASS from
   required phases, tests, Git identity, GUI actions, API/database correlation,
   restart persistence, and distinct artifacts. Record canonical per-phase JSON
   results and the complete digest-bound headless browser stage history. Never accept
   placeholder `{"result":"pass"}` evidence or screenshots as the only oracle.
9. Write the complete sanitized report with layer inventory, resolved ports
   and endpoints, baseline/promoted SHAs, phase/test/UAT outcomes, exact owned
   resource IDs, recorded validation commands, top issues and recommended
   fixes, and cleanup/retention result.
10. Every stage closes its owned browser. With `--keep`, retain the project,
    private state/evidence and owned Docker resources. Otherwise follow the
    [terminal cleanup procedure](three-tier-live.md#create-workflow): complete any required
    native cancellation/reconciliation, verify writer and lease quiescence, and
    exit the coordinator normally. Then use aggregate workspace destroy for both
    fixture classes in a finally-style path. Any cleanup failure is FAIL.

## Native launch settings

The sealed Git profile admits the documented `branch.<name>.vscode-merge-base`
history hint only when it names the immutable owned origin default. This does
not authorize commands, alter refs or relax other configuration checks. Bare
origins, unsealed profiles, conflicting hints and unknown keys remain rejected.

The native launcher validates the incoming Git environment before reducing it
to the fixed tool, locale, terminal, user-path and owned Git variables. It removes
ambient service credentials, command-injection variables and parent session
identities. The selected `CODEX_HOME` or `CLAUDE_CONFIG_DIR` must be the exact
private home of this lifecycle. It supplies `ZDOTDIR` pointing to that home,
rejects Zsh startup files there, and sets `BASH_ENV` and `ENV` to `/dev/null`.
Do not source ordinary shell startup files or copy shell snapshots. The launcher sets a private `077` umask before native execution so new logs and
receipts are private at creation. Native
workers inherit the sanitized environment and use their own host identity.

Grant the exact primary checkout, its `.git` directory, the owned sibling
`project-worktrees` parent, and this generation's private/evidence directories
through the native scoped permission profile before launch. Do not use full
access or change permissions from the product workflow.

Before a Codex launch, put these settings in its private, owner-only
`config.toml`, alongside the selected model and ordinary approval controls:

```toml
project_root_markers = [".git"]
allow_login_shell = false

[features]
shell_snapshot = false

[shell_environment_policy]
inherit = "all"
ignore_default_excludes = false
```

The launcher requires this exact shell policy without additional environment
overrides. Launch `codex --no-daemon` directly; the launcher rejects configuration,
profile, feature, remote-host and guard-bypass command-line overrides, configured
profiles, and project configuration files from cwd through the validated checkout
root, inclusive. Pin project root detection to exactly `[".git"]`; a linked
worktree ends at its own `.git` file. Configuration above that boundary is not
a project layer, and bare origins are not native launch checkouts.
Do not add project or worker configuration that changes these settings during
the trial. Verify the actual native coordinator and worker environments with
synthetic presence checks before treating propagation as qualified.
`all` here inherits the already reduced native process environment;
default exclusions add protection for later tool calls. The native host supplies
each actual session identity. Authentication stays in the protected disposable
native store. Keep unrelated credentials out of the native process; never print
environment values. Test propagation with harmless synthetic values and report
only presence/absence. For Claude, retain the same environment and startup-file
controls; Codex-specific settings are not written to its configuration. Synthetic
shell tests do not qualify effective Claude configuration or native activation.

These controls do not create a separate operating system identity or deny every
credential-file read. Follow the host's existing permissions and secret-handling
rules. Application runtime credentials remain separate private inputs to the outer
lifecycle owner, not native-launch environment additions. See the official
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
[project root detection](https://learn.chatgpt.com/docs/config-file/config-advanced#project-root-detection),
and [shell environment policy](https://learn.chatgpt.com/docs/config-file/config-advanced#shell-environment-policy).

## Headless browser contract

Use headless Playwright Test with Chrome for required web acceptance. Run
four fresh owned stage roles in order: `capability-discovery`, `evaluate`,
`uat-before-restart`, and `uat-after-restart`. Each stage starts a new process
and isolated context, records assertions, screenshots, a trace, actual browser
version and timestamps, and closes its processes even with `--keep`. Native
desktop capture, an unlocked screen, display selection, and foreground windows
are not prerequisites. Optional headless isolated Playwright MCP exploration
is separate from the acceptance oracle and cannot satisfy a required stage.

Before UAT, a new clean candidate may require another evaluation after a counted
repair. The owner verifies the current evaluation cursor, exact candidate and
fingerprints, preserves every prior PASS, and records its repair binding. At most
four additional evaluations fit the unchanged four-dispatch ceiling. Reusing a
cursor, repeating the same candidate, repeating capability/UAT, or any failed
browser attempt requires a new trial; no prior failure becomes passing. Final
qualification verifies each repeated evaluation's completed repair gate, including
archived cursors, and uses only the final two UAT receipts for restart screenshots.

The verifier freezes its independent test bundle before product implementation;
never copy it into product TDD tests or alter it to fit an implementation.
Run evaluation against the clean registered integration revision and UAT against
the clean promoted revision. The outer lifecycle owner alone restarts Compose
between the two UAT stages, retaining the original database volume. The owned
restart action observes the new loopback binding before validating deployment
and atomically records refreshed endpoints with its restart receipt. Failed
restarts invalidate earlier restart proof; public, unowned or mismatched runtime
bindings cannot produce a receipt. Recheck service health, then launch a fresh
post-restart browser against the same task ID/title/completion state.
Independent API and PostgreSQL checks accompany blank rejection, creation,
completion, and post-restart persistence. Use the frozen task-board DOM/API/table
contract in the rendered fixture objective. Before evaluation and separately before UAT,
use the declared disposable reset to establish an empty database; resets are
fixture setup, never part of the product acceptance journey. Never reset between
UAT stages.

A failed browser acceptance stage remains failed and its artifacts are preserved.
Correct the proven owner and start a new trial; no later success erases the earlier
result.
Resume retained application work with a fresh browser, never a retained tab.
Laptops must remain awake with Docker and the agent running. A locked-screen
certification additionally needs independently observed lock intervals and an
actual agent launching a new stage and collecting evidence before unlock;
a standalone browser smoke test does not certify agent continuity.

The live harness resolves the active feature through the isolated host's validated
session/run binding, accepted objective revision and immutable execution basis.
Worker access to shared application resources requires the current assignment's
exclusive runtime lease. Before promotion, Compose and
execution-phase Git evidence use the exact registered integration worktree, with
matching project, run, feature, Git directory, branch and recorded HEAD. The target must be clean except while the root checkpoint explicitly records
active `sdlc-tdd`, `sdlc-update-documents` or `align`; those authoring phases may
run validation on uncommitted integration work before coordinator sealing.
Shipping and UAT always require a clean promoted checkout. After completed promotion,
Compose uses the exact promoted primary checkout and requires integration
resource cleanup. Caller-selected paths and unrecorded descendants are rejected.

Read-only phase observations use a named private purpose. Classification permits
unsealed integration files during `sdlc-classify-failure`; TDD additionally permits
its documented `tests_red` and `tests_ready` output checkpoints before sealing.
Pending test-revision TDD uses the existing read-only revision owner to validate
its typed authority, exact test-only scope and retained quiescent writers.
Preparation uses `sdlc-prepare-execution` at its running phase or
`execution_prepared` checkpoint to observe a clean `repair_required` boundary.
It requires a completed owner replan, exact active failure and corrective-first
tasks; a plan-command repair also requires its unchanged reviewed plan and specs.
Observation never advances the correction, seals TDD or changes coordinator state.
Historical receipt inspection retains existing authoring checkpoints; fresh
preparation receipts always require a clean target.
Exact worktree, objective, basis, branch, HEAD, ownership and ancestry checks remain.
These purposes never reach Compose, inventory, build, browser or test execution;
explicit clean requests still win. Worker selectors and dirty promoted checkouts
remain rejected. Read the runtime-inventory reference before phase collection.

Pre-commit runtime testing may select a worker only through the private helper's
`run-compose --worker-task TASK-NNN --assignment-digest DIGEST -- <action>`.
Both identifiers are required. The resolver admits only an assignment holding
the exclusive runtime lease in an active scheduler slot during
`sdlc-implement-plan`, or during `sdlc-validate-codes` for a current counted
same-assignment `VALIDATION_DEFECT` recovery. The latter requires the recovery
owner's read-only proof of the blocked receipt, active dispatch, exact fresh
worker and prior termination. Both paths require a registered worktree,
unchanged assignment base, and an `ACTIVE` result from the execution owner's
live scope guard. Uncommitted changes must stay inside its write claims. This
selection never changes phase evidence or UAT targeting; after integration, rerun
the original oracle using the normal integration target. The shared runtime must
not serve concurrent worker tests.

Five screenshots mean five independently captured artifacts: four different
pre-restart UI states and one capture from the fresh post-restart stage. The
post-restart pixels may match the completed state when persistence works.
Require separate owned stage receipts and capture observations; never fabricate
visual changes, copy an earlier screenshot, or weaken the four-state check.

## Concurrent companion and final aggregate

The owned primary Git repository contains project-a for the entire SDLC app and
project-b for an independent Implement CLI. Start SDLC from the original
project-a folder in the isolated native home. Record its initial startup before
later phases. Once it is admitted, run the installed `companion_trial.py` with
`--verification-root <root> --expected-verification-id <id>` and the same selected
native home. The helper independently validates the sealed Git/native environment
and installed catalog, registers its launcher and App Servers, and fences the generation. The companion uses a separate native session and the
public Implement skill; do not manufacture its intake or completion records.
The frozen companion request prefers available native agent tools for its one
fresh assignment-only worker. Consume the admitted launch envelope's exact command
cwd and support directories with unchanged sandbox and approval controls. Verify
capabilities before arming: supported explicit command cwd and existing inherited
grants can satisfy the envelope without spawn-level setters. One task
does not require a nested CLI launch: use that fallback only when native agent
tools are unavailable and the host supports it. A missing capability or failed
launch remains a blocker with its original evidence; never switch paths as an
unadmitted retry.
The workspace records every exact launcher PID/start identity. Wait for both
native invocations and their descendants before cleanup.

Preserve all existing SDLC phases, five test classes, four browser stage roles,
startup and hook observations. Record the inner promotion with `record-git`;
its UAT remains tied to that child SHA. Let the product complete automatic
aggregation and removal, and let the companion finish its original-folder
continuation. Invoke private `record-aggregate` with the generation fence. It
requires both delivery/cleanup proofs, shared-admission overlap and unchanged
project-a content between inner UAT and the final aggregate. It never merges
or completes a product step.

Build the application again from aggregate/project-a, recreate the owned
runtime, collect a fresh acceptance inventory and rerun the five test classes.
Earlier build, UAT and test receipts stay historical. The final manifest binds
both `promoted_sha` (inner) and `aggregate_sha` (final). No old image or passing
child receipt qualifies the aggregate runtime. If app content changed after
inner UAT, preserve the failed trial and route a fresh trial through its owner.

## Task acceptance coverage

The three-tier fixture seeds an independent domain harness before its Git
baseline. `prepare-images` builds one owned nonroot dependency image and freezes
`task-acceptance-profile.json` in the private root. The native SDLC coordinator
uses it for checks on at least two admitted task assignments with distinct
trial containers, records a deferred filtering criterion,
and resolves all debt on promoted and aggregate trees. Final observation derives
coverage from owner receipts; handwritten summaries cannot satisfy this lane.
It also verifies the seeded harness bytes on both final Git candidates so a
later task cannot weaken the deferred criterion's independent assertions.
Historical original-TDD observation permits additive corrective plans only after
checking each frozen acceptance sidecar and unchanged criteria for retained tasks.
Existing promoted test classes, fresh browser stages, restart persistence,
aggregate rebuild and Implement companion remain required.

## Statistics observation

Before initial phase publication, the native coordinator inventories accepted
design and existing code through the private statistics owner. After every
coherent checkpoint it records the statistics checkpoint and emits each new
summary once. The independent phase observer captures before/during evidence;
final verification captures after evidence from the completed objective.
Missing reports cannot be supplied by handwritten summaries or patched into a
frozen trial. Cleanup archives sanitized reporting evidence automatically.
