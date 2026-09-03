# Three-Tier Live Scenario

Use this reference only for the explicit `--create`, `--create --keep`,
`--resume`, or `--destroy` modes of `$sdlc-workflow-test`. The ordinary no-flag
verifier keeps the lightweight resource-validator fixture; full live evidence
uses the v2 contract.

Before installing this hard rename, destroy any retained live environment with
the currently installed pre-rename skill. This skill recognizes only its new
ownership markers, labels, and Compose names; it has no migration or cleanup
path for old-format verifier state.

## Public modes

| Invocation | Required result |
| --- | --- |
| `$sdlc-workflow-test` | Run the existing lightweight deterministic verifier only. Do not inspect or change Docker or a browser. |
| `$sdlc-workflow-test --create` | Safely destroy the previous active exactly owned environment, create one fresh owned live project, launch one fresh verifier-owned Chrome profile/process group, run the whole SDLC and three-tier test, write the report, close that exact Chrome process group, then destroy all owned live resources even after a test failure. If prior replacement cleanup cannot be proven, do not create a new stack. Any browser or Docker cleanup ambiguity makes the run FAIL. |
| `$sdlc-workflow-test --create --keep` | Replace the previous active exactly owned environment, run the same workflow, write the report, and retain only the newly created project, private run state, two containers, network, database volume, built web image, and verifier-owned Chrome instance/profile. |
| `$sdlc-workflow-test --resume` | Revalidate and continue the one retained KEPT run only when its prior result is FAIL or PARTIAL. |
| `$sdlc-workflow-test --destroy` | Close only the exact recorded verifier-owned Chrome process group, remove the one active owned application, and archive its lifecycle state while retaining sanitized reports. Existing Chrome instances are never targets. If none exists, return `ALREADY_DESTROYED`. |

Reject `--keep` without `--create`, `--destroy` combined with `--create` or
`--keep`, and unknown lifecycle flags. A repeated create must destroy the prior
active exactly owned environment before it creates a fresh lifecycle; cleanup
ambiguity blocks the replacement so two live stacks are never intentionally
started. Existing lightweight verifier options remain unchanged when no
lifecycle action is selected. `--verification-root` may select the owned root
for a lifecycle action.

## Architecture and acceptance scope

The scenario is `three-tier-task-board-v1`:

- Frontend tier: semantic HTML/CSS/JavaScript exercised in a newly launched
  verifier-owned Google Chrome instance with a fresh isolated user-data
  directory and verification-ID window marker.
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

The private lifecycle schema is `agentic-sdlc/three-tier-lifecycle-v3` under:

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
3. Confirm Docker Engine, Docker Compose, Git, source-installed skill parity,
   canonical Google Chrome, and loopback binding support before replacing an
   active environment. Do not use an existing Chrome window for evidence.
   After `prepare`, run `launch-browser`; it invokes Chrome directly with a
   newly created private `--user-data-dir`, `--new-window`, a new process
   group, and a verification-ID marker page. Then require a successful
   computer-use `get_app_state` whose accessibility state contains that exact
   marker before any action. Tool discovery, installation, or process presence
   alone does not prove that Computer Use captured the dedicated instance.
   This initial check proves capability discovery only and is not reusable.
   A missing required live capability is PARTIAL only before an attempted
   required action; an attempted failure is FAIL.
4. Run `three_tier_lifecycle.py prepare`. It serializes lifecycle mutation,
   destroys the previous active environment through the exact ownership-checked
   standalone cleanup path, and creates a fresh lifecycle only after cleanup
   succeeds. Orphaned state, a changed project boundary, a Git remote, dirty
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
   configuration after the images are local. Use `<private-root>/codex-home` as
   the isolated `CODEX_HOME`/`--codex-home` for prompt workspace and phase state
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
8. Run GUI evaluation and UAT through `sdlc-gui-test` with
   `harness: computer-use`. Immediately before the first GUI navigation in
   `sdlc-evaluate`, and again immediately before `sdlc-uat-tests`, run a fresh
   `get_app_state` for Google Chrome. The returned accessibility state must
   contain the exact lifecycle `window_marker`; absence or ambiguity is an
   environment defect and no action may be attempted. Unless the current Codex
   surface explicitly confirms locked Computer Use is enabled for this session,
   require the console to be unlocked. A normal browser window must be visible,
   unminimized, foreground, and on the current macOS Space. Any intervening
   lock/unlock, display, Space, or browser-window change invalidates an earlier
   check. Refresh the
   accessibility tree after every successful navigation or action. Use the GUI
   for actions and independent API and PostgreSQL probes as the objective
   oracle.
9. Record capability discovery, evaluate readiness, and UAT readiness with
   `record-computer-use`. A just-in-time `cgWindowNotFound` or equivalent visibility failure is an
   `ENVIRONMENT_DEFECT` at `pre-navigation-window-capture`; record explicitly
   that no GUI navigation or action was attempted. Persist only bounded
   sanitized diagnostics: browser identity, dedicated-marker match,
   known/unknown lock and window
   visibility/frontmost/current-Space state, and returned-error versus timeout.
   A hung/timed-out call or response loss across browsers marks the shared
   Computer Use service unhealthy. Stop all further Computer Use calls in the
   attempt: no `list_apps`, new-window recovery, repeated browser retries,
   browser restart, or service restart through that path. Lifecycle cleanup may
   still close the exact owned process group without Computer Use; it fails
   closed if process identity no longer matches. Fresh-session or service
   recovery remains a separate explicitly authorized action.
10. Write the v2 semantic file incrementally so failures retain validated
    non-GUI progress, then validate `<evidence-root>/three-tier-results.json` against
   `assets/three-tier-results.schema.json`. The private helper also performs
   semantic checks and rejects placeholder files, reused generic artifacts,
   missing phases, stale Git identity, non-computer-use GUI evidence, missing
   API/database correlation, missing restart persistence, missing migration
   test evidence, fewer than five screenshots, or screenshot files without a
   recognized PNG/JPEG signature.
11. Record the dedicated browser tab with the exact `verification_id` query
    marker. In default create mode use `close-browser`, then record the tab
    closed before `finish`; in keep mode leave the exact instance running.
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

1. Pass the just-in-time Computer Use readiness gate before navigation.
2. Open the marker-bearing loopback URL in the dedicated verifier-owned Chrome
   instance only after a fresh marker-confirming capture.
3. Observe the heading, input, Add button, and empty state.
4. Submit a blank title and observe inline validation.
5. Independently prove no database row was created.
6. Create the unique task and observe its GUI row.
7. Correlate the same ID, title, and incomplete state through API and database.
8. Refresh and prove persistence.
9. Complete the task and correlate the new state through API and database.
10. Filter Active and prove the task disappears.
11. Filter Completed and prove the task reappears.
12. Restart both services without deleting the volume.
13. Reload and prove the completed task persists.
14. Capture sanitized empty, validation, created, completed-filter, and
    post-restart screenshots.
15. Close the exact verifier-owned Chrome process group for default create;
    retain it for keep. Never use Quit, `killall`, or another broad browser
    close that could affect existing Chrome instances.

Screenshots alone never establish PASS.

## Destroy workflow

1. Validate the verification-root marker, active lifecycle schema, exact run
   path, run/project markers, verification ID, and Compose project. A Git
   project must remain remote-free; a kept project must also be clean so user
   changes are never silently deleted. Recorded browser instance identity is an
   exact cleanup gate.
2. Revalidate the recorded Chrome PID, process group, executable path, and
   verifier-owned user-data directory, then signal only that process group.
   Never target existing Chrome processes, profiles, or tabs. Identity
   ambiguity stops cleanup before Docker mutation.
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
  --verification-root <root> --expected-verification-id <id> launch-browser
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
