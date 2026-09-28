# Three-Tier Live Process

Use this process only after explicit `--create-live-test`, `--create-live-test --keep`, or
`--resume`:

1. Read `references/three-tier-live.md`. Confirm Docker, Compose, Git, Node.js
   20+, npm, installed Chrome and source-installed skill parity. Preparation
   freezes the independent Playwright Test bundle; `run-browser-stage --stage
   capability-discovery` proves actual headless interaction and screenshot
   capture. Missing capability before an attempt is PARTIAL; an attempted
   required stage failure is FAIL.
2. For create modes, prepare one owned lifecycle with
   `scripts/three_tier_lifecycle.py`. Its `prepare` action serializes lifecycle
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
   the helper's `prepare-images` action to pull only the fixed
   public base images through an owned empty Docker CLI config; do not reuse
   that config for Compose. For resume mode, require the existing lifecycle to
   be owned, KEPT, and previously FAIL or PARTIAL; revalidate its project
   boundary and recorded resources without creating replacements. Use the
   private root's isolated agent home for all prompt workspace and phase state;
   never reuse or delete the user's ordinary Agentic SDLC run directory.
3. Create the project through the normal prompt-bound Agentic SDLC workflow:
   first run `$sdlc-start workspace init <project-folder>`, then use
   `scripts/render_three_tier_prompt.py` to replace the generated starter body
   while preserving its managed identity, and finally run
   `$sdlc-start run <prompt-ref-or-file>`. Follow the returned phase
   skill; do not make the lifecycle helper or hooks orchestrate phases.
   Before dispatching that phase, record the startup phase while its checkpoint
   still names `sdlc-start`, before plans or execution state exist and before
   Git leaves the fixture baseline. The lifecycle observes exact JSON ownership
   and real hook discovery and retains its digest-bound startup receipt. It
   rejects later phase PASS and final PASS without this receipt. A late repair
   starts a fresh trial and does not rehabilitate earlier evidence.
4. Build all three logical layers: browser GUI, Django/Gunicorn web/API server,
   and PostgreSQL. Run exactly two labelled Compose containers, dynamically
   publish only the web port on loopback, and keep PostgreSQL private to the
   Compose network. Run every Compose action through the helper's
   generation-locked `run-compose` action with the immutable expected
   verification ID; never invoke a mutating `docker compose` command directly.
   This makes a replacement wait for an in-flight action and prevents a
   superseded invocation from starting or changing a stack. Record containers
   with the role-specific `--web-container` and `--database-container`
   arguments; the helper verifies Compose service labels before accepting them.
   When an authorized configuration repair replaces the Compose network,
   inspect service aliases before retrying a failed startup. If a reused
   container lost its service alias, use owned `up --force-recreate --detach
   --wait` for both services, retaining the database volume, then rerun the
   original checks. Preserve the failed startup as environment-recovery
   evidence. Docker documents container recreation and volume preservation in
   its [Compose up reference](https://docs.docker.com/reference/cli/docker/compose/up/).
   After switching from a worker to integration, or from integration to the
   promoted checkout, recreate both services through owned `up --force-recreate
   --detach --wait`, preserving the volume. Also do this after a dependency
   configuration change: reused containers can retain the earlier Compose file
   path and dependency labels. Inspect those labels before a restart oracle.
   Rediscover the dynamic web port after every recreation and restart.
5. Execute every phase and test class in the scenario reference. Local ship
   means build and run the promoted clean SHA locally; it never means push,
   publish, PR creation, or PR merge.
6. Route web evaluation and UAT through `sdlc-gui-test` with
   `harness: playwright-test` and `headless: true`. Use the owned stage command
   at the matching phase; see the headless contract below.
   Build the clean selected target through owned `build web` or `up --build`
   before recording runtime inventory. The owner binds its immutable image ID
   to that checkout revision and rechecks image, mounts and endpoint ownership
   at browser launch and every independent checkpoint.
7. Preserve failed receipts, traces and reports. Recovery cannot relabel a
   failed trial. Browser cleanup validates the exact owned process identity;
   ambiguity is `CLEANUP_FAILED`.
8. Persist semantic results incrementally using
   `agentic-sdlc/three-tier-results-v3`. Failed runs retain validated partial
   layer/test status; the lifecycle helper derives PASS from
   required phases, tests, Git identity, GUI actions, API/database correlation,
   restart persistence, and distinct artifacts. Record canonical per-phase JSON
   results and the four digest-bound headless browser stage receipts. Never accept
   placeholder `{"result":"pass"}` evidence or screenshots as the only oracle.
9. Write the complete sanitized report with layer inventory, resolved ports
   and endpoints, baseline/promoted SHAs, phase/test/UAT outcomes, exact owned
   resource IDs, recorded validation commands, top issues and recommended
   fixes, and cleanup/retention result.
10. Every stage closes its owned browser. With `--keep`, retain the project,
    private state/evidence and owned Docker resources. Otherwise destroy the
    exact owned runtime in a finally-style path. Any cleanup failure is FAIL.

## Headless browser contract

Use headless Playwright Test with Chrome for required web acceptance. Run
four fresh owned stages in order: `capability-discovery`, `evaluate`,
`uat-before-restart`, and `uat-after-restart`. Each stage starts a new process
and isolated context, records assertions, screenshots, a trace, actual browser
version and timestamps, and closes its processes even with `--keep`. Native
desktop capture, an unlocked screen, display selection, and foreground windows
are not prerequisites. Optional headless isolated Playwright MCP exploration
is separate from the acceptance oracle and cannot satisfy a required stage.

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
contract in the generated prompt. Before evaluation and separately before UAT,
use the declared disposable reset to establish an empty database; resets are
fixture setup, never part of the product acceptance journey. Never reset between
UAT stages.

A failed stage remains failed and its artifacts are preserved. Correct the
proven owner and start a new trial; no later success erases the earlier result.
Resume retained application work with a fresh browser, never a retained tab.
Laptops must remain awake with Docker and the agent running. A locked-screen
certification additionally needs independently observed lock intervals and an
actual agent launching a new stage and collecting evidence before unlock;
a standalone browser smoke test does not certify agent continuity.

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

Five screenshots mean five independently captured artifacts: four different
pre-restart UI states and one capture from the fresh post-restart stage. The
post-restart pixels may match the completed state when persistence works.
Require separate owned stage receipts and capture observations; never fabricate
visual changes, copy an earlier screenshot, or weaken the four-state check.
