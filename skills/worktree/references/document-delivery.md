# Document programs and protected delivery

This is a shared contract, not another coordinator. Agentic SDLC owns its run;
ordinary Implement continues to use a singleton FEAT execution. Private helper
actions require the bound native coordinator. Never invoke either coordinator
recursively.

## Intake and source coverage

Extract obligations first, then reconcile the supplied architecture against
them. Preserve accepted technology and contract decisions. The root routes
canonical REQ and FEAT updates through the SDLC adapters and the sole publisher,
`maintain-project-specs`. A worker reports a typed specification gap and fences
new dispatch; it never edits canonical specifications.

Copy authorized normative assets into `docs/contracts/` in the target project.
Keep requirements/design concise and preserve original source identifiers.
Never commit private source material merely because intake can read it. Use an
authorized portable snapshot; local paths and credentials are not portable.

The accepted objective uses `workflow/accepted-objective-v3` and binds:

```json
{
  "inputs": {
    "manifest": "docs/contracts/manifest.json",
    "manifest_sha256": "<SHA256 of manifest bytes>",
    "program_sha256": "<workflow_state.digest of private program>"
  }
}
```

Read [phase planning](phase-planning.md) to preserve supplied milestones or derive
missing ones before freezing the program. The public manifest schema is
`maintain-project-specs/source-bundle-v2`:

```json
{
  "schema": "maintain-project-specs/source-bundle-v2",
  "planning": {
    "mode": "derived",
    "sources": ["docs/contracts/acceptance.json"],
    "rationale": "One coherent accepted outcome requires one milestone.",
    "phases": [{"id": "Core", "depends_on": [], "exit_criteria": ["AC-001"]}]
  },
  "sources": [{
    "path": "docs/contracts/acceptance.json", "sha256": "<source digest>",
    "authority": "normative", "role": "acceptance", "ids": ["AC-001"],
    "references": [], "id_field": "id"
  }],
  "coverage": [{
    "source_id": "AC-001", "kind": "acceptance", "disposition": "required",
    "requirements": ["REQ-001"], "features": ["FEAT-001"],
    "reason": "Required accepted behavior"
  }],
  "conflicts": []
}
```

Inventory roles: requirements, design, contract, acceptance, context. Authority
is normative or informative. Coverage kinds: acceptance, scoped-check,
release-gate, decision, contract. Dispositions: required, deferred, rejected;
all need a reason, and required obligations need valid REQ/FEAT mappings.
Every normative identifier needs one disposition. JSON catalogs require exact
recursive ID-field extraction. Prose identifiers and reference lists are a
root-reviewed inventory, not a claim of automatic semantic completeness.
Explicitly inspect links, schema references, normative contradictions and
duplicate definitions; unresolved conflicts or missing references block intake.
Per-file limit is 8 MiB; the package limit is 64 MiB. The objective stays small.

Private `admit-inputs` validates and stages exactly the manifest-listed snapshot
plus canonical pair, after rejecting unrelated repository changes; it never
commits or changes HEAD. Preparation owns the subsequent scoped contract commit.
Requirements readiness verifies source bytes first. Full design mappings and
program admission are required before objective settlement/planning. Use private
`workflow_intake.py --owner agentic-sdlc admit-program --run-dir … --candidate …`.
Steering-fenced admission is allowed only after writer quiescence; it does not
lift the fence. Source/program changes require an accepted revision, impact
analysis and fresh affected plans/evidence. Historical failed evidence remains. Completed slice contracts and order are
immutable; added or changed obligations need a new corrective slice. Historical
merge facts never substitute for current source qualification.

## Program and task identities

Private `workflow/program-v2` has `phases` and `slices` arrays and an explicit `uat_criteria` catalog. A phase has `id`,
`depends_on` and full source `exit_criteria`. A slice has `id: SLICE-001`, `phase`,
`depends_on`, `feature_ids`, `requirement_ids`, `criteria`, `regression_criteria`
and `change_kind` (non-runtime, runtime, deployment, security or data). Each slice
also freezes `implementation_coverage`, mapping every non-UAT source criterion
and regression criterion to nonempty IDs in the frozen required wave checks.
Campaign selection never removes a required implementation check. Declare
slices in topological delivery order, respecting phase barriers. Plan every
required obligation, but elaborate detailed tasks only for upcoming slices.
The phase definitions must exactly match the manifest's reviewed `planning`
record on admission and revalidation. Early source inventory may use null
planning; it cannot admit a program. Finalizing or changing planning requires
an accepted manifest revision. No old-manifest compatibility path is provided.

Task plans retain `Requirements:` and add `Features:`. Every slice requirement
and feature must be mapped by its tasks. Freeze membership before dispatch;
mixed slices never create umbrella FEAT records. A failed member blocks its
candidate. Removing it changes the accepted plan and invalidates evidence.

Use `--execution` for private execution/repair helpers. Execution identities
are FEAT or SLICE IDs; product mappings remain FEAT IDs. Current state uses
`current_execution`. Current schemas are enumerated in the
[execution plane](../../sdlc-prepare-execution/references/execution-plane.md);
obsolete active runs are retained and rejected for explicit recovery. There is
no automatic conversion or compatibility alias.

One candidate runs at a time. Tasks in eligible features may run in parallel
when dependencies, write claims, contracts and runtime resources allow it.
Freeze independent worker, wave and retained-worktree limits using the
[wave contract](worker-waves.md), with defaults 4/8/16. Unknown host capacity
permits one total active worker; known zero waits. Reserve each entire wave
against run-wide retained and uncertain resources before allocating its first
worktree. Every task has its own worktree.
Each batch shares one frozen base, integrates deterministically, passes combined
checks and cleans its workers before the next batch starts from the validated
integration tip. Preserve immutable assignments, writer termination checks,
repair budgets and checkpoint recovery. Continuation limits are checkpoints,
not permission to remove no-progress or failure budgets.

Dependency handoffs contain only direct dependencies and digest-bound result
references. Load detailed evidence on demand. Shared decisions stay in the
canonical design/contract snapshot, not copied into every worker prompt.

## Qualification and completion policy

Default completion is `reviewed-pr`, with optional accepted `live_operations`
and `allow_nonruntime_na`. A cluster supplies a target, not merge authority.
Remote automatic completion requires this accepted run-level policy:

```json
{
  "boundary": "merge-verified", "repository": "owner/repository",
  "base": "main", "method": "squash", "delivery_scope": ["SLICE-001"],
  "live_operations": ["exercise-disposable-service"],
  "merge_effects": ["skills-merge-pr.yml", "skills-merge-completion.yml", "ci.yml"],
  "allow_nonruntime_na": true
}
```

Bind each specific PR to that accepted objective revision; do not ask again
inside its scope. Changed authority requires a new accepted revision. Only
unmanaged source checkouts support unattended delivery initially. Preserve the
explicit outer-worktree integration boundary.

UAT is optional and follows [the shared policy](optional-uat.md). Do not require
live infrastructure for ordinary implementation admission. A supplied environment
alone requests neither testing nor a provisioning skill.

Private `admit-delivery` validates task/wave evidence and executes the frozen
required core checks on the final clean promoted candidate. It binds each source
implementation criterion through `implementation_coverage`, with executed check
IDs, outcomes, source tree, objective and basis. This is implementation evidence;
it has no invented runtime artifact/environment fields. Phase-only implementation
checks use `admit-phase` with frozen `checks`. Preserve all failed core attempts.

An explicitly local program uses `completion: {"boundary": "local-promoted",
"live_operations": [], "allow_nonruntime_na": true}`. Add only the accepted
local runtime operations when needed. This policy requires an automatic managed
outer; reject unsupported ownership at program admission before execution.
After each slice's promotion and cleanup,
call private `admit-delivery`, then `complete-local` with a candidate containing
that exact `admission` reference. The owner binds the clean promoted commit/tree,
finished coordinator, frozen execution basis, accepted revision and executed
checks into `workflow/local-delivery-completion-v1`. It grants no remote effects.
Remote attest/submit rejects this policy before contacting a service.

The next local slice reuses the managed outer and starts from its verified prior
promotion. Do not aggregate or release the outer between slices. Historical
receipts remain verifiable after later accepted revisions and checkout cleanup;
completed slice definitions remain immutable. Changed obligations require a new
corrective slice. `phase-exit` consumes the same admission references for either
completion boundary. Aggregate preparation and whole-program completion require
every scheduled slice, every phase exit, and current source coverage.

Only requested ready UAT uses a separately frozen
`workflow/qualification-plan-v3` and `qualify --execution OBJECTIVE`. Its reviewed
Python harness owns candidate, independent observation, assertions and backend
cleanup as described in `optional-uat.md`. The selected backend is `existing`,
`container` or `nebius`; the latter two require explicit skill invocation. Missing
scope/runtime prerequisites produce pending status, not repeated repair attempts.
Complete exact-candidate execution and owner cleanup are required for a UAT PASS.
Do not relabel historical, skipped or intervened tests as current passing evidence.

`attest-delivery` consumes an implementation `admission` reference and complete
current `skills-review/v2` report from `review-pr`. It posts a COMMENT attestation,
not a GitHub approval. The protected SDLC record contains the admission digest,
source head/tree, policy-bound UAT disposition and implementation coverage.
Unrequested or pending UAT permits otherwise eligible publication. Actual product
failures and unproven cleanup remain separately owned obligations.
Default reviewed-PR completion needs no merge broker. Stop at this resumable
delivery checkpoint; do not declare the whole program or its features complete.
Further slices wait for independently verified merge evidence.

`submit-delivery` independently checks promoted/reviewed/tested identity, source
basis, current base, CI, objections and conversations, allowed operators and
accepted effects. It requires an already-installed protected policy declaring
`sdlc_contract: workflow/sdlc-delivery-v2`. Queue rules are rejected. The shared
broker owns actual approval/protected direct merge, obeying repository rules.
No guard weakens protections or installs workflows automatically.

Submission is journaled before dispatch. `reconcile-delivery` binds the returned
broker run or uniquely recovers its exact immutable intent after ambiguity.
Never repeat an uncertain dispatch. Submitted, merged and result-verified are
different states. Failed result CI leaves merged-verification-failed, pauses
dependent delivery, and resumes verification without repeating merge.

After verified merge, the next source branch starts at the current fetched
default branch. The first candidate preserves the selected branch/history.
`phase-exit` consumes exact `admissions` references and completed slice records.
A merged slice records progress; feature/phase completion requires all of its
implementation obligations. Retain optional UAT independently. Keep candidate readiness and production qualification
as separate phases when the source package defines them.

## Shared skill caller contracts

- `sdlc-gather-context` reads local material first; calls `research` for a bounded
  unresolved evidence question, then writes its own context pack.
- `sdlc-create-design` calls `design` in advisory mode and selected specialists;
  no nested planner, implementation coordinator or independent spec publication.
- Validation uses report-only `code-review`, check-only lint and relevant domain
  skills. Keep TDD, test execution, evaluation and UAT as distinct evidence.
- `align` includes advisory source → REQ → FEAT → slice → task → test traceability;
  it returns changed paths and affected gates to the root for revalidation.
- Diagnosis returns to the existing classifier/repair owner. PR delivery reuses
  protected primitives beneath `sdlc-merge-pr`; never nest ordinary `merge-pr`.
