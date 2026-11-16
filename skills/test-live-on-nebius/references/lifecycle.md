# Lifecycle and adapter contract

The helper stores runs under the selected native agent home's `nebius-live-runs`.
Run IDs are UUIDs. State, plan hashes, resource ledger and quota receipts remain
outside Git. Never copy credentials into the journal. Each run has one flock
owner; resume and cleanup fail with RUN_BUSY while it is held.

A normalized scenario contains `tenant`, `project`, `region`, resolved `engine`,
`source`, `objective`, a nonempty `acceptance` string list, `resources` entries
with `kind`, positive `count`, and `owner` (sdk/cxcli/borrowed), an absolute
`adapter` path and `adapter_sha256`. Add exact service-specific settings under
`inputs` and a required nonempty `source_identity` mapping for source/build
identity. The host verifies that mapping before admission; each adapter verifies
its service-specific artifact identity. The generic runner records the mapping
but cannot independently attest an arbitrary project build. Never insert secrets.
The agent creates this file only after deriving and reviewing the scenario.
Declare additional imported files in `dependency_sha256` as absolute path to
SHA256 mappings. Admission adds the mandatory helper closure and records the
interpreter identity and installed package versions. It retains exact helper
bytes privately and verifies the manifest before every callback. Drift blocks
effects; explicit recovery can restore reviewed bytes from the archive. Never
overwrite shared source automatically. Package versions do not attest every
installed dependency byte; use a controlled environment and preserve its lockfile.

Adapters expose five functions, taking `(journal, monotonic_deadline)`:

- `preflight`: return True only after auth, ancestry, quota, source, dependencies
  and execution capability checks. Return `awaiting_quota` only with no created
  resources. The host performs the verified browser request when authorized.
- `provision`: perform only declared creates using native owners and persist
  acceptance immediately. Respect the deadline before each effect/wait.
- `verify`: return True only after all declared assertions pass. Persist concise
  independent evidence; avoid raw resource responses or secret output.
- `reconcile`: reconcile operation receipts and exact run labels plus parent and
  baseline; never adopt by name alone. Wait accepted operations terminal before
  allowing cleanup. Mark an undispatched/confirmed absent intent failed, otherwise
  keep it unresolved. Never blindly resubmit an uncertain intent.
- `cleanup`: stop the exact owned process group, reconcile outstanding operations,
  delete owned resources in reverse dependency order, and call `journal.absent`
  only after authoritative absence. Return True only when writers are quiescent.

The adapter is privileged reviewed Python; these callbacks are a cooperation
contract, not confinement. Review imports, subprocesses, paths, parent IDs,
resource counts and all mutation calls. Freeze the exact bytes and dependencies.
A generated adapter must include cleanup and reconciliation before first create.
Resume after provisioning began performs reconciliation and cleanup; start a new
trial for uncontaminated product evidence.

For SDK writes, inventory the relevant resource kind before dispatch and retain
that baseline. `scripts/sdk_actions.py` adds an exact intent label and persists
receipts. A lost response is reconciled through project/run/intent labels, the
baseline and the resource-specific operation service. Ambiguous results remain
cleanup_pending. A new deletion attempt is allowed only after authoritative
terminal failure and fresh ownership validation.
For a lost deletion receipt, the exact ledger resource ID and authoritative
absence can resolve the intent without resubmitting it. A still-present resource
with no attributable operation remains unresolved.

For SDK writes, call `journal.intent` with a digest of the request and the complete
operation `descriptor` (action, kind, create parent/dependencies or exact delete
target) in the same atomic write before
submission. Supply its stable key and an `on_accepted` callback to the sibling
SDK `submit` helper. That callback records both operation receipt and created
resource identity before waiting. Mark terminal success/failure after authoritative
observation. Never mark an ambiguous network failure terminal merely to unblock
cleanup. The SDK supplies idempotency metadata; it is not a license to retry.

For cxcli, preserve the frozen config, generated manifest, module identities and
backend mapping. Record exact process-group ownership before launch. Native state
and labels reconcile partial apply. Stopping a parent process alone does not
prove descendants or cloud writes stopped. Never delete backend evidence while
an owned resource or accepted operation is unresolved.
The subprocess supervisor watches its parent's liveness pipe and stops its
owned group on parent death. Resume proves the saved group absent; it never
signals a persisted PID that could have been reused. A still-active or ambiguous
group blocks cleanup until its original supervisor ends it or the owner proves
quiescence. A crash before gate release cannot start the external command.

Cleanup has its own 30-minute budget. An uncatchable process death leaves state
for `resume`/`cleanup`; no background daemon is installed. Retain failed trials.
A recovery that changes product-owned behavior requires a new trial after cleanup.
