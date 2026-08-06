# Live Multi-Tier Test

## Ownership model

Use the lifecycle helper for every mutation. Keep its generation ID immutable
for the invocation. The helper owns the canonical private root, seeded project,
isolated Codex home, Compose snapshot, report archive, and cleanup state.

The generated Compose model must use the helper-provided unique project name
and label every service, network, volume, and locally built image with:

```text
org.openai.task-implementer-test.generation=<generation-id>
```

Each locally built image must also carry the exact helper-provided Compose
project in `org.openai.task-implementer-test.compose-project`; the helper
discovers and deletes images only when both labels agree. Names alone never
prove ownership. Require `compose.yaml` to contain strict
JSON syntax using long-form ports and mounts so the helper can validate it
before Docker parses it. Call `validate-compose` and then `compose-up`; the
helper digest-pins the canonical Compose model, records live ownership before
the first Docker mutation, asks Docker for a loopback port, and inspects the
post-start resource identities. If validation or Docker inspection fails, do
not claim live PASS.
Before invoking Compose, the helper allowlists the exact top-level, service,
build, port, mount, network, and volume keys needed by the fixture. This rejects
includes, extends, label files, external build/cache inputs, build
entitlements, privileged or host-network builds, host networking or namespaces,
privileged containers, added capabilities, devices, bind mounts, external
networks/volumes, build contexts outside the project, and any published port
that is not exact loopback.
Docker Compose may materialize absent `command` and `entrypoint` fields as null
and network `ipam` as an empty object in canonical JSON. Accept only those
empty defaults in the canonical pass; the strict raw-source pass must reject
authored keys, and canonical non-null runtime or non-empty IPAM overrides
remain forbidden.

After `workspace init`, render the app template into the generated managed
prompt with `scripts/render_app_prompt.py`. Preserve its
`task-implementer/prompt-v1` frontmatter and identity fields; replace only the
editable body.

## Seeded brownfield fixture

`prepare` copies `assets/multi-tier-fixture/` into a new local-only Git
repository and commits it on a named test branch. The seed is a minimal local
single-process task board. The managed prompt asks Task Implementer to migrate
it into a frontend, API, and PostgreSQL application with a dependent runtime
integration slice.

The first logical dependency wave is parallel-capable but runtime capacity may
split it into batches. Batching must not change its logical wave. The later
integration task owns Compose, end-to-end checks, and shared runtime docs.
Before planning, repeat the frontend container-port-80 requirement in the
frontend task's self-contained assignment so the worker never needs the full
managed prompt.

The canonical four-task plan must repeat these verifier contracts before IDs
lock:

- frontend: own only `app/frontend`, serve and expose container port 80, proxy
  `/api` to service `api:8000`, and never run Docker;
- API: own only `app/api`, implement health/create/list/update, connect to
  PostgreSQL service `db` with the `task_test` database/user/password, and
  create the required table idempotently without relying on a bind mount;
- database: own only `app/database` and define the exact integer/text/boolean
  `tasks` schema without requiring a host mount at runtime;
- integration: depend directly on all tier tasks and own only `compose.yaml`
  plus repository tests. Its assignment repeats the exact three service names,
  `postgres:16-alpine`, strict service/build/port/mount allowlists, generation
  and Compose-project labels, one shared labelled network, one labelled named
  PostgreSQL volume, loopback target port 80 with no authored published port,
  and no API/database host ports. Every service must declare that network with
  long-form object-map syntax and an empty options object; list syntax is not
  accepted by the pre-Compose ownership validator.

Reject planning before dispatch if any of those constraints is absent from the
applicable task. The managed prompt is not worker context and cannot repair an
under-specified assignment later.
Task Implementer must not start Docker or containers; the verifier owns runtime
launch only after promotion and worktree cleanup.
Give workers fresh assignment-only context, leave queued assignments unarmed
until a worker slot exists, and require `task-start` as the first private
transition after immediate Git identity verification. Use the assignment's
exact embedded helper/workspace paths and pass its embedded digest unchanged;
the helper performs canonical validation, so do not recompute the digest with
ad hoc JSON. Read the incoming handoff and perform deeper preflight afterward. Observe private
liveness every 30 seconds. Stop
the disposable generation without recovery if an armed worker's `task-start`
misses 60 seconds, on a 240-second stale heartbeat, after its immutable `standard`
300-second or dependent `integration` 420-second read-only/no-file budget, or
on total worker timeout. After Task Implementer
promotes and cleans the workspace, continue immediately with runtime checks and
sanitized report generation.
At the matching 240- or 360-second read-only warning, demand an immediate
claimed-file edit or blocker. Reject autonomous or background heartbeat loops.
Require single-use `task-start` and reject out-of-claim mutations as progress.

## Runtime checks

Use the Docker-assigned loopback port returned by `compose-up`. Do not publish
PostgreSQL. After startup, call lifecycle `collect-application`; it owns these
generation-fenced checks:

1. Confirm all services are healthy.
2. Confirm frontend delivery, then exercise the proxied API path.
3. Create, list, and update a task through the API.
4. Query PostgreSQL inside its container and correlate the same task ID and
   values.
5. Restart only the API service and confirm the task remains available.

Treat `docker compose ps --format json` as JSON Lines, as documented by Docker;
the helper may also accept a single JSON object or array for compatibility with
older local Compose output shapes.

Project unit and end-to-end tests remain Task Implementer validation evidence;
the lifecycle collector does not rerun them after Docker starts. Repeat the
unchanged Task Implementer prompt as a separate public `run` invocation before
`validate-results` and require `ALREADY_COMPLETE`.

Store only bounded structured results and artifact paths under the run evidence
directory. Validate the final manifest through lifecycle `validate-results`;
PASS finish requires its recorded digest. Never copy prompt bodies, dotenv
files, credentials, raw service logs, or private orchestration state into the
report.

Record each non-lifecycle-owned stage immediately through the generation-fenced
`record-stage` action. The report must render the full ordered stage matrix,
including each tier worker and wave integration result, one explicit failure
analysis section, and all downstream NOT_RUN stages. If normal report creation
fails, pass the exact failed stage and bounded reason to `finish`; its fallback
uses the same durable ledger and must remain equally specific.

## Cleanup

Plain create always finishes through exact cleanup, including after a failed
test. Keep mode retains the current generation. Destroy validates the current
owner, marker, generation, owned local-origin Git identity, Compose snapshot, and
runtime labels, then removes only that generation. Any ambiguity retains state
and returns a cleanup blocker.
