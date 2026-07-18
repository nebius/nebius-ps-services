# Three-Tier Live Scenario

Use this reference only for the explicit `--create`, `--create --keep`, or
`--destroy` modes of `$agentic-sdlc-test`. The ordinary no-flag verifier keeps
the lightweight resource-validator fixture and existing live-results v1
contract unchanged.

## Public modes

| Invocation | Required result |
| --- | --- |
| `$agentic-sdlc-test` | Run the existing lightweight deterministic verifier only. Do not inspect or change Docker or a browser. |
| `$agentic-sdlc-test --create` | Create one owned live project, run the whole SDLC and three-tier test, write the report, close the dedicated tab, then destroy all owned live resources even after a test failure. If an unhealthy Computer Use service prevents safe tab closure, retain the runtime as `CLEANUP_FAILED` for separately authorized recovery. Any cleanup failure makes the run FAIL. |
| `$agentic-sdlc-test --create --keep` | Run the same workflow and write the report, but retain the project, private run state, two containers, network, database volume, built web image, and dedicated browser tab. |
| `$agentic-sdlc-test --destroy` | Close the exact retained tab, then remove the one active owned application and archive its lifecycle state while retaining sanitized reports. If none exists, return `ALREADY_DESTROYED`. |

Reject `--keep` without `--create`, `--destroy` combined with `--create` or
`--keep`, unknown lifecycle flags, and a second create while an active
application exists. Existing lightweight verifier options remain unchanged
when no lifecycle action is selected. `--verification-root` may select the
owned root for a lifecycle action.

## Architecture and acceptance scope

The scenario is `three-tier-task-board-v1`:

- Frontend tier: semantic HTML/CSS/JavaScript exercised in Microsoft Edge;
  Google Chrome is a recorded fallback.
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

The private lifecycle schema is `agentic-sdlc/three-tier-lifecycle-v1` under:

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
agentic-sdlc-test.verification-id=<recorded-verification-id>
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
   one supported browser, loopback binding support, and no active lifecycle.
   Before `prepare`, require an actual successful computer-use `get_app_state`
   for the selected browser; retry the same call by bundle identifier when the
   display-name call returns normally with a failure. Tool discovery, app
   installation, or process presence alone does not prove a capturable window.
   This initial check proves
   capability discovery only; it is not reusable readiness for later GUI work.
   A missing required live capability is PARTIAL only before an attempted
   required action; an attempted failure is FAIL.
4. Run `three_tier_lifecycle.py prepare` and use its exact project root,
   private root, verification ID, and Compose project. Immediately run the
   helper's `prepare-images` action. It pulls only the fixed public Python and
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
   $sdlc-start run <prompt-path-or-unique-filename>
   ```

   Do not add a workflow CLI or let the lifecycle helper call SDLC phases.
6. Follow the returned phase skills through requirements, context, design,
   steering, planning, execution preparation, TDD, implementation, validation,
   tests, evaluation, documents, alignment, commit, local ship, UAT, and the
   final document pass. Do not push, publish, create a PR, or merge a PR.
7. After each phase, record a concise sanitized summary and existing relative
   evidence paths with `record-phase`. Record the clean baseline/promoted Git
   identity, loopback endpoints, Compose-internal database endpoint, and exact
   labelled Docker inventory. Use `--web-container` and
   `--database-container`; the helper verifies canonical
   `com.docker.compose.service` roles and does not infer roles from argument
   order. Record each sanitized validation command, status, and summary with
   `record-validation`; never persist credentials or secret-bearing arguments.
   PASS requires at least one validation record and requires every recorded
   validation to pass. Record the clean baseline SHA before implementation,
   then refresh `record-git` with the clean promoted SHA after final sealing;
   failed pre-promotion reports must still retain the known baseline identity.
8. Run GUI evaluation and UAT through `sdlc-gui-test` with
   `harness: computer-use`. Immediately before the first GUI navigation in
   `sdlc-evaluate`, and again immediately before `sdlc-uat-tests`, run a fresh
   `get_app_state` for the exact selected browser. Unless the current Codex
   surface explicitly confirms locked Computer Use is enabled for this session,
   require the console to be unlocked. A normal browser window must be visible,
   unminimized, foreground, and on the current macOS Space. Any intervening
   lock/unlock, display, Space, or browser-window change invalidates an earlier
   check. Refresh the
   accessibility tree after every successful navigation or action. Use the GUI
   for actions and independent API and PostgreSQL probes as the objective
   oracle.
9. A just-in-time `cgWindowNotFound` or equivalent visibility failure is an
   `ENVIRONMENT_DEFECT` at `pre-navigation-window-capture`; record explicitly
   that no GUI navigation or action was attempted. Persist only bounded
   sanitized diagnostics: browser identity, known/unknown lock and window
   visibility/frontmost/current-Space state, and returned-error versus timeout.
   A hung/timed-out call or response loss across browsers marks the shared
   Computer Use service unhealthy. Stop all further Computer Use calls in the
   attempt: no `list_apps`, new-window recovery, repeated bundle/browser
   retries, tab close, browser restart, or service restart through that path.
   Preserve the owned runtime and hand off fresh-session or service recovery as
   a separate explicitly authorized action.
10. Validate `<evidence-root>/three-tier-results.json` against
   `assets/three-tier-results.schema.json`. The private helper also performs
   semantic checks and rejects placeholder files, reused generic artifacts,
   missing phases, stale Git identity, non-computer-use GUI evidence, missing
   API/database correlation, missing restart persistence, missing migration
   test evidence, fewer than five screenshots, or screenshot files without a
   recognized PNG/JPEG signature.
11. Record the dedicated browser tab. In default create mode close it before
    `finish`; in keep mode leave it open and record it as retained.
12. Finish the lifecycle and present the report path, application layer
    inventory, resolved ports/endpoints, Git SHAs, phase/test/UAT results,
    validation commands, top issues and recommended fixes, owned resources,
    retention state, and cleanup outcome.
13. Without `--keep`, call destroy in a finally-style path after both success
    and failure. If an unhealthy Computer Use service prevents safe tab
    closure, the destroy gate must fail before Docker deletion, retain the
    runtime as `CLEANUP_FAILED`, and require separately authorized recovery. A
    cleanup failure overrides the test result and makes the final status FAIL.

## Exact GUI UAT

Use one unique non-secret task title and a clean database volume:

1. Pass the just-in-time Computer Use readiness gate before navigation.
2. Open the loopback URL in the dedicated Edge tab, or recorded Chrome
   fallback.
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
15. Close the dedicated tab for default create/destroy; retain it for keep.

Screenshots alone never establish PASS.

## Destroy workflow

1. Validate the verification-root marker, active lifecycle schema, exact run
   path, run/project markers, verification ID, Compose project, and browser-tab
   state. A Git project must remain remote-free; a kept project must also be
   clean so user changes are never silently deleted.
2. Use `computer-use` to close only the recorded dedicated tab. Never close the
   browser application or unrelated tabs.
3. Inspect every recorded Docker ID. A present resource must carry both exact
   ownership labels. Any mismatch fails closed before deletion. A missing
   recorded resource is already absent and is not replaced by name discovery.
4. Remove only recorded containers, network, volume, and built web image, in
   dependency order. Never remove Docker itself, shared/base images, unrelated
   images, unrelated volumes, or PostgreSQL outside the recorded Compose
   project.
5. Verify every recorded ID is absent, then remove only the exact owned run
   directory. Archive lifecycle state, refresh the sanitized report, and
   remove `active.json`.
6. On partial cleanup, retain active state with `CLEANUP_FAILED`, list remaining
   exact IDs, and return FAIL so a later destroy can safely resume.

The helper commands are private implementation mechanics:

```bash
python3 agentic-sdlc-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> prepare --browser edge
python3 agentic-sdlc-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> status
python3 agentic-sdlc-test/scripts/three_tier_lifecycle.py \
  --verification-root <root> destroy
```

Use `--help` for the record and finish subcommands. Do not expose these helper
subcommands as a replacement for `$sdlc-start`.
