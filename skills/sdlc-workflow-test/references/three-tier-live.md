# Three-Tier Live Scenario

Use this reference only for the explicit `--create-live-test`, `--create-live-test --keep`,
`--resume`, or `--destroy` modes of `$sdlc-workflow-test`. The ordinary no-flag
verifier keeps the lightweight resource-validator fixture; full live evidence
uses the v3 contract.

The earlier `agentic-sdlc-test` to `sdlc-workflow-test` skill-name migration
requires destroying retained old-format environments with the old skill first.
This skill cannot adopt their ownership markers, labels or Compose names. The
`--create-live-test` action rename leaves current lifecycle ownership unchanged.

## Public modes

| Invocation | Required result |
| --- | --- |
| `$sdlc-workflow-test` | Run the existing lightweight deterministic verifier only. Do not inspect or change Docker or a browser. |
| `$sdlc-workflow-test --create-live-test` | Safely destroy the previous active exactly owned environment, create one fresh owned live project, run four fresh owned headless Playwright Test stages, run the whole SDLC and three-tier test, write the report, close that exact Chrome process group, then destroy all owned live resources even after a test failure. If prior replacement cleanup cannot be proven, do not create a new stack. Any browser or Docker cleanup ambiguity makes the run FAIL. |
| `$sdlc-workflow-test --create-live-test --keep` | Replace the previous active exactly owned environment, run the same workflow, write the report, and retain only the newly created project, private run state, two containers, network, database volume, built web image. Every browser stage closes, including keep mode. |
| `$sdlc-workflow-test --resume` | Revalidate and continue the one retained KEPT run only when its prior result is FAIL or PARTIAL. |
| `$sdlc-workflow-test --destroy` | Close only the exact recorded verifier-owned Chrome process group, remove the one active owned application, and archive its lifecycle state while retaining sanitized reports. Existing Chrome instances are never targets. If none exists, return `ALREADY_DESTROYED`. |

Reject `--keep` without `--create-live-test`, `--destroy` combined with `--create-live-test` or
`--keep`, and unknown lifecycle flags. A repeated create must destroy the prior
active exactly owned environment before it creates a fresh lifecycle; cleanup
ambiguity blocks the replacement so two live stacks are never intentionally
started. Existing lightweight verifier options remain unchanged when no
lifecycle action is selected. `--verification-root` may select the owned root
for a lifecycle action.

## Architecture and acceptance scope

The scenario is `three-tier-task-board-v1`:

- Frontend tier: semantic HTML/CSS/JavaScript exercised in a fresh verifier-owned
  headless Chrome process and isolated context with a digest-bound stage receipt.
- Application tier: Django and Gunicorn serving the GUI plus a versioned REST
  API.
- Data tier: PostgreSQL with a committed migration.
- Runtime: exactly two Docker Compose containers: one web and one database.

The application must create and list tasks, reject blank titles, persist data,
mark a task complete, and filter active or completed tasks. Logical tiers are
not container count: browser code is the presentation tier even though the web
container serves its assets.

Use a Docker-assigned web host port, bind it only to loopback, and resolve it
with `docker compose port`. Do not host-publish PostgreSQL. Require service
health checks and database readiness before the web service starts.

## Ownership and private state

The private lifecycle schema is `agentic-sdlc/three-tier-lifecycle-v4` under:

```text
<verification-root>/three-tier-live/
|-- active.json
|-- lifecycle/<verification-id>.json
|-- reports/<verification-id>/report.md
`-- runs/<verification-id>/
    |-- .agentic-sdlc-three-tier-run.json
    |-- project/
    |-- private/
    `-- evidence/
```

There is at most one active application per verification root. Keep raw logs,
prompts, plans, screenshots, transcripts, database contents, and SDLC state
inside the owned run. Keep only the sanitized report and lifecycle archive
after destruction.

Every owned Docker resource must have both exact labels:

```text
com.docker.compose.project=<recorded-compose-project>
sdlc-workflow-test.verification-id=<recorded-verification-id>
```

Record exactly two container IDs, one private network ID, one database volume
ID, and one built web image ID before the environment can pass. Never infer
ownership from a name prefix alone.

## Create workflow

1. Parse and validate the public flags before reading or mutating lifecycle
   state.
2. Run the unchanged no-flag deterministic verifier. Stop before live mutation
   on any deterministic FAIL.
3. Confirm Docker, Compose, Git, Node.js 20+, npm, installed Chrome and
   source-installed parity. After preparation, run the owned headless
   `capability-discovery` stage. No desktop capture prerequisite applies.
4. Run `three_tier_lifecycle.py prepare`. It serializes lifecycle mutation,
   destroys the previous active environment through the exact ownership-checked
   standalone cleanup path, and creates a fresh lifecycle only after cleanup
   succeeds. Preparation creates the marker-only Git baseline and its one owned
   local bare origin before normal prompt intake. Orphaned state, a changed
   project boundary, an external or extra Git remote, dirty
   kept work, a resource-label mismatch, or incomplete cleanup blocks the new
   lifecycle. Cleanup removes the union of recorded aliases and resources
   discovered by both exact ownership labels, so an interruption after Docker
   creation but before inventory capture cannot leave a second owned stack.
   Every present alias is ownership-checked, canonicalized by Docker identity,
   and deduplicated before removal. Sanitized prior reports and lifecycle
   history remain. The prior verifier-owned Chrome process is closed only after
   its exact PID, process group, executable, and profile are revalidated. Use
   the returned exact project root,
   private root, verification ID, and Compose project. Immediately run the
   helper's `prepare-images` action with the returned verification ID supplied
   as `--expected-verification-id`. Treat that ID as the immutable generation
   fence for this invocation; every later mutating helper action, including
   resume, must supply it and fail with `STALE_THREE_TIER_GENERATION` after a
   newer create. It pulls only the fixed public Python and
   PostgreSQL images with a bounded timeout and an owned empty Docker CLI config
   so a user credential helper cannot block public pulls. Never export or reuse
   that private config for `docker compose`; Compose uses the ordinary CLI
   configuration after the images are local. Use `<private-root>/<agent>-home` as
   the selected isolated native home (`CODEX_HOME` or `CLAUDE_CONFIG_DIR`,
   passed to the verifier with `--agent-home`) for prompt workspace and phase state
   so destroy never targets the user's ordinary `~/.codex/sdlc-runs` tree.
5. Use the existing prompt-bound workflow only. Initialize the workspace first,
   then run `scripts/render_three_tier_prompt.py` against the returned starter.
   The renderer preserves its four managed identity fields and replaces only
   the scenario template's five named placeholders: project root, private root,
   evidence root, verification ID, and Compose project. Intake must accept the
   rendered file as revision `r0001` before phases begin:

   ```text
   $sdlc-start workspace init <project-folder>
   $sdlc-start run <prompt-ref-or-file>
   ```

   Do not add a workflow CLI or let the lifecycle helper call SDLC phases.
   Run every Docker Compose action through the helper's generation-locked
   `run-compose -- <compose-action>` command. Do not run mutating
   `docker compose` commands directly. The helper fixes the project name and
   project directory, rejects file/project/scale overrides, holds the lifecycle
   lock for the action, and rejects a superseded verification ID before the
   action begins. Read-only non-Compose Docker inspection may remain direct.
6. Follow the returned phase skills through requirements, context, design,
   steering, planning, execution preparation, TDD, implementation, validation,
   tests, evaluation, documents, alignment, commit, local ship, UAT, and the
   final document pass. Do not push, publish, create a PR, or merge a PR.
7. After each phase, record a concise sanitized summary and one canonical
   `agentic-sdlc/phase-result-v4` JSON result with `record-phase`. Bind it to
   the lifecycle verification ID, immutable baseline SHA, phase-time
   `recorded_head`, and the exact phase-specific assertion list. Phase results
   must be private, bounded, regular, single-link files. Record the clean baseline/promoted Git
   identity, loopback endpoints, Compose-internal database endpoint, and exact
   labelled Docker inventory. Use `--web-container` and
   `--database-container`; the helper verifies canonical
   `com.docker.compose.service` roles and does not infer roles from argument
   order. Record each sanitized validation command, status, and summary with
   `record-validation`; never persist credentials or secret-bearing arguments.
   PASS requires at least one validation record and requires every recorded
   validation to pass. Record the clean baseline SHA before the first phase,
   then refresh `record-git` with the clean promoted SHA after final sealing.
   Earlier phase artifacts stay immutable: finalization proves each recorded
   head descends from the baseline and is an ancestor of the promoted head;
   failed pre-promotion reports must still retain the known baseline identity.
   Seal and revalidate any review repair on a clean integration SHA before GUI
   evaluation; do not leave a reviewed repair uncommitted behind an unrelated
   environment failure.
8. Run `run-browser-stage --stage evaluate` at evaluation, using the clean
   integration target. Run `uat-before-restart` at UAT against the promoted SHA.
   Before each of these targets is evaluated, use owned `run-compose -- build web`
   followed by owned `up --detach` and runtime inventory recording. A clean
   owned `up --build --detach` also records build proof. The immutable receipt
   binds the exact checkout and SHA to the built image ID. Browser launch and
   every API/database checkpoint recheck the running web container image,
   ownership, absence of web mounts and loopback endpoint bindings. A dirty
   worker build is useful for development but cannot certify acceptance.
9. Use generation-locked `run-compose -- restart` to restart both services,
   retain the same volume, rediscover `port web 8000`, and refresh runtime
   endpoints. Then run `uat-after-restart` with a new browser. The helper binds
   it to the pre-restart record and the owned restart receipt.
10. Write the v3 semantic manifest and validate its exact headless stage,
    Git identity, API/database, restart and five-screenshot evidence. Preserve
    partial results and failed receipts; caller booleans never replace them.
11. Every stage closes its own browser, including keep mode. `close-browser`
    is the private recovery action for an interrupted owned process.
12. Finish the lifecycle and present the report path, application layer
    inventory, resolved ports/endpoints, Git SHAs, phase/test/UAT results,
    validation commands, top issues and recommended fixes, owned resources,
    retention state, and cleanup outcome.
13. Without `--keep`, call destroy in a finally-style path after both success
    and failure. Destroy revalidates and closes only the recorded Chrome PID
    and process group; ambiguity fails before Docker deletion as
    `CLEANUP_FAILED`. A cleanup failure overrides the test result.

## Exact GUI UAT

Use one unique non-secret task title and a clean database volume:

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
between the two UAT stages, retaining the original database volume. Rediscover
the dynamic loopback port, refresh recorded endpoints, then launch a fresh
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

Screenshots alone never establish PASS.

## Destroy workflow

1. Validate the verification-root marker, active lifecycle schema, exact run
   path, run/project markers, verification ID, and Compose project. A Git
   project must retain exactly its one owned local bare origin, private receipt,
   frozen baseline/default refs and executable push-rejection guard. Foreign or
   redirected transports, borrowed objects and unsafe paths fail before any
   browser or Docker effect. A kept project must also be clean so user
   changes are never silently deleted. Recorded browser instance identity is an
   exact cleanup gate.
2. Revalidate the recorded Chrome PID, process group, executable path, and
   verifier-owned user-data directory, then signal only that process group.
   Never target existing Chrome processes, profiles, or tabs. Identity
   ambiguity stops cleanup before Docker mutation.
   If an earlier close exited before its state was saved, fresh proof that
   both the recorded PID and process group are absent completes browser
   cleanup without a signal. A remaining group, reused PID, or failed
   inspection remains a blocker.
3. Combine every recorded Docker alias with resources discovered through both
   exact ownership labels. Reinspect every present alias, require both labels,
   resolve the canonical Docker identity (`Id`, or volume `Name`), and
   deduplicate by kind plus canonical identity before deletion. Any mismatch or
   discovery failure stops cleanup.
4. Remove only that verified union of containers, network, volume, and built
   web image, in dependency order. Never remove Docker itself, shared/base
   images, unrelated images, unrelated volumes, or PostgreSQL outside the
   recorded Compose project.
5. Verify every owned ID is absent, then remove only the exact owned run
   directory. Archive lifecycle state, refresh the sanitized report, and
   remove `active.json`.
6. On partial cleanup, retain active state with `CLEANUP_FAILED`, preserve the
   cumulative removed/already-absent ledger plus current remaining exact IDs,
   and return FAIL so a later destroy can safely resume. Treat a failed remove
   as already absent only when a fresh inspect proves the resource is gone.

The helper commands are private implementation mechanics:

```bash
python3 sdlc-workflow-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> prepare
python3 sdlc-workflow-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> --expected-verification-id <id> run-browser-stage --stage capability-discovery
python3 sdlc-workflow-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> status
python3 sdlc-workflow-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> --expected-verification-id <id> \
  run-compose -- up --detach
python3 sdlc-workflow-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> destroy
```

Use `--help` for the record and finish subcommands. Every mutating subcommand
other than prepare and destroy requires `--expected-verification-id`. Do not
expose these helper subcommands as a replacement for `$sdlc-start`.

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
