# Factual workflow statistics

Statistics are observational. They cannot accept work, schedule workers, consume
repair allowance, authorize recovery or invalidate steering. There is no ETA or
predicted duration. Component groups do not change vertical slices or serial waves.
Implement and SDLC use one projector, journal and inventory validator. Status
reports observed running workers separately from reserved worker slots.

## Before execution

After objective intake and before SDLC first phase initialization or Implement
`prepare-specs`, the coordinator
inspects the accepted design and existing code and supplies a private architecture
inventory through the existing intake/context/design owners. Record all known
architecture even if task decomposition has not begun. Do not infer components
from task count or directory names alone. Use stable readable IDs such as
`LAYER-service` and `COMP-task-api`; retain those identities across revisions.

The private candidate uses `workflow/architecture-inventory-v1` with `layers`,
`components` and `gaps` lists. Layer rows contain `id`, `name`, `origin` and
`sources`. Component rows additionally contain `layer` and `features` (`FEAT-*`
references). Origin is `declared` for accepted planned architecture and `observed`
for existing implementation. The same ID may have one row of each origin;
conflicting names or layer assignments remain explicit disagreements. Sources are
objects with `kind`, a relative `path`, its exact `sha256` and a readable `anchor`.
Use `kind: project` for accepted design or existing code in the registered
checkout. Before project documents exist, declared architecture may cite
`kind: objective` with the current accepted `objectives/rNNNN.json` snapshot
path from `objective.json`. This exposes only its reference and digest, never
the private snapshot content. An objective alone cannot prove existing code.
Use public-safe names and anchors; never include URLs, credentials or raw source.
Record unresolved architecture and unplanned scope in `gaps`.

Invoke the private owner using the bound native identity:

```bash
python3 <skills-root>/worktree/scripts/workflow_statistics.py inventory \
  --run-dir <bound-run> --candidate <private-inventory.json>
```

This validates exact source bytes in the registered checkout or accepted objective. It creates
an immutable initial report before phase execution. Refresh the inventory through
the same command after context/design or observed code changes; earlier snapshots
remain immutable. Missing facts remain unavailable, never an invented empty
architecture. An initializer without inventory reports that gap explicitly.
Refresh the inventory's gap statements after plan acceptance or revision and
recovery when earlier uncertainty has been resolved. Preserve source-bound
architecture facts; remove only gaps contradicted by newly observed owner facts.
The projector does not reinterpret free-text gaps as current evidence.

## During execution

Locked tasks add `Name`, `Primary component` and `Touched components` fields.
Primary component uses one inventory `COMP-*` reference or explicit `unknown`;
touched components are additional references or `none`. Count each
execution/task identity once by its primary component. Show known totals alongside
undecomposed slices and unassigned tasks. Planning eligibility is distinct from
observed worker overlap.

Planned and prepared task definitions use the execution owner's canonical
fingerprint. Preparing an unchanged locked plan does not create task scope
changes or rewrite the initial baseline.

After publishing each coherent checkpoint and current state, including plan
acceptance/revision, blocking, recovery and terminal outcomes, call:

```bash
python3 <skills-root>/worktree/scripts/workflow_statistics.py checkpoint \
  --run-dir <bound-run>
```

Intake and execution owners also record successful transitions automatically.
Existing pause/resume adapters record explicit pauses. `SDLC_STATISTICS` or `IMPLEMENT_STATISTICS` output
contains a deduplicated summary ID and saved report reference. Show each new
summary in chat once, including checkpoint command results; repeated observations
do not create history. If reporting returns unavailable, report that measurement
gap and continue the owning workflow. Do not route it to product repair control.

## Reports and status

Private `statistics/` contains immutable revision-bound `events/`, source-bound
`inventories/`, `initial.json`/`initial.md`, replaceable `current.json`/`current.md`,
immutable `terminal/` reports, and deduplicated `notifications/`. These files are
outside decision evidence and plan fingerprint inputs. Initializer admission is
limited to this recognized safe storage shape and pre-execution observations.

Existing `$sdlc-start status [project-folder]` and
`$implement status [project-folder]` return the same deterministic
projection. It acquires an existing read lock without creating files and reports
unavailable on contention or incomplete checkpoint publication. It never advances
execution or refreshes reports. Submission, independent acceptance, integration,
promotion and cleanup are separate outcomes.
Canonical worker-result receipts distinguish submitted and blocked results from
running work, acceptance and integration. A terminated worker may still reserve a
scheduler slot until its owner reconciles it; reserved slots are not live workers.
Summaries show current and retired task counts, accepted results before retirement,
and the unique logical task total across both. A replacement task with the same
identity counts once in that total; its retired result never accepts the replacement.
Counted repair dispatches come from the repair owner's immutable receipts and
remain separate from successful worker transfers and measured recovery operations.
Reports observe these records without changing their budgets or authority.

Timing uses monotonic nanoseconds with opaque machine/boot/clock provenance and
UTC timestamps for presentation. Process-only fallback cannot bridge restarts;
reboot or missing boundaries produces gaps. Report whole-workflow elapsed,
per-phase/per-attempt intervals, pauses, accumulated task time, interval union
and observed overlap separately. These are elapsed observations, not CPU usage
or productive effort. Inclusive stage counters are supplemental. Interrupted,
blocked and failed runs remain explicitly partial. Task completion is not a
percentage of elapsed time.

Peak observed worker count is the maximum running count across preserved coherent
owner states and the current snapshot. It remains available when incompatible
clocks prevent measuring overlap duration. Reserved, blocked and submitted tasks
do not count as running workers. A zero-duration point sample does not supply
missing elapsed intervals; duration totals remain unavailable if only such samples
are measurable and other intervals are missing. Fully measured zero-length
attempts still have a known zero duration.

Ordinary checkout cleanup retains private reports. The disposable verifier
archives sanitized statistics before removing its owned native homes. Preserved
reports and failed/interrupted trial outcomes are never converted to current proof.

Clock semantics: [Python 3.12 monotonic clocks](https://docs.python.org/3.12/library/time.html#time.monotonic).

## Implement and transition overhead

Implement phases come from specification bootstrap, execution, wave validation,
promotion and automatic delivery receipts. Its completeness concerns the accepted
objective, not the entire application; scope remains unplanned until plan acceptance.
Corrections retain earlier executions and follow the current corrective execution.
Integrated tasks do not prove aggregation or cleanup. Missing boundaries remain
unavailable, and late inventory cannot manufacture the original initial report.
Ownerless or mixed-owner observation history is unsupported and returns unavailable;
restore a matching quiescent bundle instead of converting retained history.

Heartbeats preserve liveness updates without full reporting scans. Refill records
one completed transition while distinct launch, result, acceptance, recovery and
delivery events remain separate. Basis reuse is confined to the same protected
transition; later transitions reverify all inputs. Recheck after external validation
or relevant mutation. Retain full history checks; no persistent validation cache.

Benchmark frozen working-byte versions with identical repeated fixtures. Separate
added reporting cost from optimization gains and instrumented cost attribution from
unprofiled latency. Never infer a speedup from fewer function calls alone.

## Attributed owner timing

`workflow/timing-v2` batches preparation, admission, launch, validation, repair,
integration, cleanup, observed tools and coordination-wait counters per owner
transition. Lock measurements cover acquisition only. First/last clock stamps
bound observations; each measured duration requires comparable monotonic clocks.
Unknown samples are counted explicitly. These inclusive totals can overlap and
must never be added to infer whole-workflow elapsed time. Unobserved native tools
remain unknown; verifier receipts own preflight collection, queue and execution
timings. Telemetry errors cannot change product results or recovery authority.

Execution Git commands, including commit hooks and byte-exact seal-message
plumbing, use the shared bounded process owner without a duration deadline.
Ordinary hook descendants must be quiescent before returning or initiating Git
recovery. Tool counters include these observed execution commands.
