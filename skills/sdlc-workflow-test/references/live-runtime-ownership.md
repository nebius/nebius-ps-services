# Live runtime ownership

Required before three-tier startup, phase collection or runtime targeting.

The live harness resolves the active feature through the isolated host's validated
session/run binding, accepted objective revision and immutable execution basis.
Worker access to shared application resources requires the current assignment's
exclusive runtime lease. The worker acquires and holds it through the full live
sequence and teardown; the outer verifier consumes it without impersonating or
releasing worker ownership. A per-resource use lock guards synchronous actions
against lease release, while short state locks leave heartbeat/status available.
Every live startup requires owned
`up --force-recreate --detach --wait web db`; build web separately.
The helper rejects other startup shapes without rewriting arguments.
Recreation retains the named database volume; rediscover endpoints and
inventory afterward. UAT persistence uses the separate owned restart action.

Before promotion, Compose and
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
Exact worktree, objective, basis, branch, HEAD, ownership and ancestry checks remain.
These purposes never reach Compose, inventory, build, browser or test execution;
explicit clean requests still win. Worker selectors and dirty promoted checkouts
remain rejected. Read [runtime inventory](runtime-inventory.md) before phase collection.

For final historical TDD validation, follow the exact lineage checks in
[the live contract](three-tier-live.md). Never rewrite old receipts
or use preserved TDD evidence to admit stale application acceptance.

Before dispatching the first phase, record `sdlc-start` PASS while the coordinator
checkpoint still names `sdlc-start` and Git remains at the fixture baseline.
Supply the actual native identity through the initial recorder's private
`--coordinator-session` selector; never impersonate it in the outer environment.
The lifecycle independently checks JSON ownership and source state-loader
discovery in a `workflow-startup-v3` receipt. This is not native hook activation.
After naturally emitted tool/hook events exist, collect `record-native-activation`
with the immutable verification ID; the separate native receipt correlates the
owned Codex session and executed-command item IDs with installed hook events without copying prompt bodies.
The current adapter supports Codex CLI 0.167.0; unavailable host/event formats
remain a qualification gap. Full three-tier PASS requires both receipts. Every later phase PASS and
final semantic ingestion require that receipt. Empty or mismatched ownership
also blocks runtime targeting. A late ownership repair requires a fresh trial;
it cannot restore full-workflow validity to earlier evidence.

Pre-commit runtime testing may select a worker only through the private helper's
`run-compose --worker-task TASK-NNN --assignment-digest DIGEST -- <action>`.
Both identifiers are required. The resolver admits only the assignment holding
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
