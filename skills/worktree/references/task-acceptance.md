# SDLC task acceptance

This contract applies to Agentic SDLC. Ordinary Implement keeps its existing
task, no-change and combined-wave validation. Optional provider live testing can
still be invoked manually before a PR; neither workflow automatically provisions
Nebius resources.

## Freeze applicability before execution

Alongside `<plan>.validation.json`, SDLC requires `<plan>.acceptance.json`. It is
part of the execution basis and each immutable assignment. Missing policy fails
preparation. Do not modify an existing task's policy during replanning; add a
corrective task while retaining prior evidence and deferred obligations.

```json
{
  "schema": "workflow/task-acceptance-policy-v3",
  "tasks": {
    "TASK-001": {
      "requires": [],
      "criteria": [{
        "id": "title-normalization",
        "purpose": "implementation",
        "disposition": "check",
        "reason": "The task implements a directly executable domain behavior",
        "prerequisite": null,
        "resolution_stage": null,
        "check": {
          "kind": "command",
          "definition": {
            "id": "title-normalization",
        "purpose": "implementation",
            "argv": ["{python}", "-B", "tests/check_title.py"],
            "execution_policy": {"observation_interval_seconds": 30, "deadline": null},
            "environment_keys": []
          }
        }
      }]
    }
  }
}
```

Cover every task with nonempty criteria and execution-local unique criterion IDs.
Classify each `purpose` as `implementation` or `uat`; only implementation checks
run before worker success. Follow [optional UAT](optional-uat.md). A criterion is:

- `check`: executable now, using a frozen command or container check.
- `not_applicable`: a specific reason, `check: null`, and null prerequisite/stage.
  Documentation-only work can qualify; runtime difficulty is not N/A.
- `deferred`: a named missing prerequisite, an executable future check, and
  `resolution_stage` of `before_consumer` or `before_delivery` for implementation,
  or `uat` for optional acceptance. A runnable assertion
  failure is never deferred. The coordinator owns this disposition.

`requires` names implementation criteria whose evidence a task consumes. Every
consumer must also name its producer in the task dependency graph, including
deferred prerequisites. Optional UAT cannot satisfy a correctness dependency. Deferred
prerequisites block their consumers; unrelated tasks may continue. All deferred
criteria from historical bases remain obligations, including corrected plans.
Coordinator replan history also requires bases for plans retired before dispatch;
refreshing an unchanged plan cannot alter its frozen acceptance policy.

## Execute before worker success

The running native worker invokes the private transition helper's `task-accept
--assignment <canonical-assignment>`. The owner supervises frozen checks and
records immutable attempt evidence separately from the native worker result.
Do not add fields to native result JSON or submit handwritten PASS receipts.
On failure, report `blocked` through the existing native result and use failure
classification/recovery. Do not rerun the same failed attempt or weaken policy.

The source snapshot includes all nonignored repository files, bytes and modes,
using a private Git index. The caller's index remains unchanged. Git filters that
transform tested bytes, escaping symlinks and submodules are unsupported. Tests
must not change source. Task finish checks the tested tree before committing,
then binds it to the committed result; admission and historical replay verify
the same producer evidence. No tests execute after `task-finish`.

Existing no-change acceptance and combined-wave checks still run. A task receipt
does not certify the integrated product or replace explicitly requested meaningful-scope UAT.

## Isolated container trials

Use `test-software-in-container` to determine applicability and review the
project harness. Its evidence helpers assess inventory and results; they are not
a universal execution command. SDLC's narrow Docker adapter accepts a prebuilt
local image ID (`sha256:...`), `linux/amd64` or `linux/arm64`, positive numeric
`uid`, explicit `collect_argv`/`run_argv`, `execution_policy`, `suite_sha256`,
`harness` path-to-SHA256 mapping, `runner` name/version, and `resource` identity.
Set `kind: container`. Declare `runtime:<resource>` in that task's conflict domains.
Use distinct resource identities for independent tasks.

The reviewed image must provide `/workspace` and a nonroot-writable `/evidence`,
with no declared volumes. Dependency/image preparation belongs to the project;
this adapter never builds, pulls, publishes, provisions or authenticates.
It copies the exact Git snapshot, disables network and healthchecks, drops all
capabilities, forbids privilege escalation and mounts, and bounds CPU, RAM and
processes. At most two task trials run concurrently in a run. Host memory
supervision remains mandatory and belongs to the caller or declared outer owner:
obtain a fresh successful physical-host observation before launch, monitor
throughout the work, and cancel exact owned work on critical or unknown results.
A sandboxed worker relies on that actual owner without repeating an unavailable
host probe. Without an active owner and reliable visibility, heavy work remains
blocked. Different runs also obey their shared host limits. See the
[host-memory contract](../../global-context-management/references/host-memory.md).
The adapter supervises the Docker client and independently cleans its exact
container; it does not install a second host-memory monitor inside the sandbox.
Exported snapshot directories use `0755`, regular files retain their Git
executable mode, and the copied collection inventory uses `0644`, so the declared
nonroot UID can read them after [Docker copy](https://docs.docker.com/reference/cli/docker/container/cp/).
The enclosing host temporary directory remains `0700`; native private umasks,
logs and receipts are unchanged.

Collection and test execution use distinct fresh writable containers, sharing
only the verified immutable image layers. The harness reads these owner inputs:
`SDLC_ACCEPTANCE_PHASE`, `INPUT_SHA256`, `SUITE_SHA256`, `SELECTION_SHA256`,
`CASE_ID`, `PLAN_SHA256`, `OUTPUT`, and `INVENTORY`, each with the
`SDLC_ACCEPTANCE_` prefix (the first already includes it).
Collection writes a sealed `test-software-in-container.inventory.v2` to OUTPUT;
execution reads INVENTORY and writes `test-software-in-container.suite-result.v2`.
Use the container skill's [suite contract](../../test-software-in-container/references/suite-evidence.md).
The supervisor independently observes process exit and cleanup and supplies
completion fields. Empty, skipped, incomplete, interrupted or failed suites do
not pass. A container that never started fails at the launch boundary before
receipt retrieval; a completed failing test still retains its structured result.
Receipt retrieval bounds the archive stream before accepting its single
regular JSON file (at most 1 MiB); it never extracts container paths onto the host.
Hashes detect drift; they are not signatures against an actor who can
rewrite the private owner state. Container evidence is Linux userspace proof,
not cloud, kernel, hardware or production qualification.

## Resolve mandatory debt and retain optional UAT

The coordinator uses private `acceptance-resolve --run-dir <run> --candidate
<registered-project-directory>`, optionally repeating `--criterion
<execution-id>:<criterion-id>` for prerequisite subsets. Run it on a clean,
quiescent registered integration, promoted or aggregate project directory.
Resolution is bound to the exact repository tree and criterion definitions.
Replaying a passed resolution does not rerun effects; failed evidence remains
immutable and requires the existing corrective workflow. Unknown process or
container termination retains ownership and blocks dependent transitions.

For a proven environment failure, the classifier may issue one counted
`acceptance_retry_same_tree` dispatch. Bind the failure event's
`evidence_digests.task_acceptance` to the failed receipt or interrupted operation
digest, establish an authorized environment remedy, then pass the canonical
dispatch path through `--repair-dispatch <path>`. Retry each failed trial
separately with the same tree and checks. The owner consumes the dispatch once,
retains prior evidence and records measured remediation time. Interrupted
publication of verified evidence can replay without checks or another dispatch.
Product defects still require the existing corrective plan or run.

Before publication, reconcile every mandatory implementation obligation against
the exact candidate. Optional UAT debt remains recorded without blocking eligible
implementation or delivery. Requested final-scope UAT covers the agreed journeys
when ready. Missing capability is pending, never fabricated PASS or N/A.
Qualification and aggregate review freeze mandatory obligation snapshots for
historical verification. Later core obligations require fresh implementation
admission without rewriting earlier evidence. Missing or changed bases cannot
mean zero debt. Task-local UAT IDs never reclassify another slice's source criteria;
only the accepted program's explicit UAT catalog classifies source obligations.
