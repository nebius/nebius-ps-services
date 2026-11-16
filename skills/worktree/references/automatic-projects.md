# Automatic project workspaces

Implement and SDLC use this same private lifecycle. A user starts or continues
from an original project folder; the coordinator records and manages every path.
Use separate native sessions for independent projects. Do not start a supervisor
or ask the user to invoke Worktree for ordinary workflow execution.

## Intake and discovery

On a named non-default primary branch with committed history, acceptance
automatically admits a clean logical project and creates one outer worktree.
Unrelated sibling folders may be dirty under the scoped admission contract below. Keep the session's launch
directory in the original folder. Use the returned execution project for all
product work and the original folder for later continuation. Native permissions
must include the owned checkout parent, common Git directory and private home.
Never broaden native permissions from inside the product workflow.

Identity is common Git directory plus repository-relative project scope. The
registry spans Implement and SDLC in one selected native home. Equal, ancestor,
descendant and whole-repository scopes conflict; siblings may run concurrently.
Additional out-of-project write claims reserve their repository-relative paths
before the immutable plan is accepted. A conflicting shared-file claim waits
for its owner. Do not change another project's files without that claim.

Use the private intake `status --project-root <original>` for public
`status [project-folder]`. It reports objective, owning workflow, phase, worker
count, wait reason and the next original-folder command. It never creates work.
A root-folder status lists all projects. Cross-owner continuation names the
correct public workflow.

Admission precedes Git allocation. At most four objectives retain an admitted
slot in one home; later objectives queue without allocating a checkout. Capacity
is reserved atomically across all projects: four active task workers and sixteen
retained task worktrees, further bounded by fresh native capacity and each run's
W/B/P policy. Unknown capacity permits at most one shared worker. Coordinator,
integration and outer checkouts are separate bounded overhead. Rotate new-wave
admission after full-wave cleanup; do not stop running workers for fairness.
Typed revision reconciliation releases exactly terminated worker slots and
rotates that superseded wave's turn. Its retained checkouts still consume
physical capacity until the shared [archive owner](revision-retirement.md)
verifies their removal before a replacement wave.

Default/unknown-default branches retain their existing delivery policy. A
manually created Worktree is an explicit external owner: do not adopt it as an
automatic outer. Unsupported active schemas require a quiescent upgrade; preserve
their records, worktrees and proof. There is no legacy execution path.

## Scoped automatic admission

Before automatic allocation, inspect the selected project, effective instructions,
canonical specs and every shared input used by its build, tests or tooling. If the
original has unrelated dirt, provide a complete committed dependency declaration
through private intake `accept --input-dependencies <private-json>` (or `continue`
when a queued run needs a refreshed declaration). This is a coordinator input,
not a public skill flag or a user-maintained file.

The declaration has exactly `schema: "workflow/input-dependencies-v1"`,
`base_head` (the captured original commit), `complete: true` and `claims`:
repository-relative `{kind: "exact" | "prefix", path: "..."}` objects. Include
shared root configuration, lockfiles, packages and tools actually read; if the
input set is unknown, block rather than asserting completeness. Selected-project
dirt and effective ancestor instruction/spec dirt are protected implicitly. A
whole-repository project has no unrelated sibling dirt. Both sides of a rename
count, as do staged, unstaged and untracked changes. Explicit ignored inputs
cannot bypass the check. Ignored generated files are not automatically inputs.
Git reports dirty submodule content at the gitlink path; that dirty parent also
blocks declared inputs nested beneath it. This does not broaden write ownership.
Scoped input checks explicitly include submodule dirt even when local Git
configuration normally hides it.
Declared reads crossing a gitlink in the committed snapshot or current index
fail with `UNSUPPORTED_SUBMODULE_INPUT`, consistent with the existing write
boundary. Parent-repository checks cannot certify submodule contents, including
ignored files; automatic intake does not initialize or validate submodules.

The Worktree owner checks the bound coordinator, project registry, declaration,
branch and exact base before allocation and again under its lifecycle lock.
The outer starts from that committed base; it never imports uncommitted files.
A queued source advance needs a newly source-bound declaration. New shared read
or external write claims must be checked before use; changes after basis freeze
require accepted, fenced replanning with quiescent writers. Each execution keeps
an immutable declaration archive so later revisions cannot invalidate history.

Manual `$worktree add` and final original-branch promotion still require the
**entire original checkout** to be clean. Ordinary original-checkout work may
continue after admission; the owned execution stays bound to its committed base.

## Execution and automatic aggregation

1. Complete normal inner execution: integration checkout, frozen task waves,
   worker termination/admission, combined checks, final alignment, honest optional-UAT disposition and docs.
   Keep final evidence in the coordinator's sealed record. Promote each completed
   execution to the outer and remove its exact worker/integration resources.
2. For `run.automatic_outer=true`, do **not** release the outer lease or emit a
   manual Worktree handoff. Call private intake `aggregate-prepare --run-dir <run>`.
   This fences new writers and retains the lease as aggregation-ready.
3. Wait if the source is dirty, switched, reserved by another delivery, or in a
   Git operation. Never commit, stash, discard, switch or import its dirty files.
   A descendant source tip is valid. Deletion, rename or rewritten history
   requires an explicit ownership decision.
4. The helper freezes current aggregate plus child in a separate candidate.
   Resolve a merge conflict only in that exact candidate and through the existing
   Worktree candidate owner; preserve its journal. Never edit the aggregate.
5. Call `aggregate-validate --run-dir <run>`. It executes all frozen combined
   acceptance checks on the exact candidate. Inspect the combined diff and run
   report-only final alignment, acceptance/UAT and documentation review there.
   Do not edit candidate source during review. Bind actual child gate evidence
   separately from aggregate reviews using `aggregate-record-gates` below.
   A changed candidate cannot reuse passing evidence. If UAT was requested and its
   meaningful scope and runtime prerequisites are ready, run private intake
   `qualify --execution OBJECTIVE --stage aggregate`. Otherwise retain pending or
   not_requested disposition and continue eligible delivery. Worker dispatch stays
   fenced while the coordinator tests the aggregate. Inner UAT is not aggregate UAT.
6. Call `aggregate-finish --run-dir <run>`. Exact source/child/candidate checks
   protect promotion. The helper persists delivery proof, releases the same
   lease, removes the exact clean outer/candidate resources and releases capacity.
   Then use ordinary intake `complete`; completion independently verifies proof.
7. If source advancement invalidates a candidate, repeat prepare/validate/finish.
   The initial candidate permits at most two automatic drift rebuilds, persisted
   across continuation. Further churn waits for an explicit retry decision.
   Do not use a drift retry to erase failed acceptance.
8. A proven acceptance failure uses Troubleshoot. Call private
   `aggregate-correct --run-dir <run> --execution <fresh-FEAT-ID>` after diagnosis.
   It retains the failed operation and reservation, fast-forwards the owned outer
   to the frozen combined candidate and starts a fresh execution basis.
   For Implement, run prepare-specs for that new ID before a new locked plan.
   For SDLC, route the corrective feature through its normal owning phases.
   Never edit a sealed plan, reopen its executor, or reacquire a released lease.
   Finish the corrective execution before preparing another combined candidate.

Interrupted writes replay their durable intent. A missing checkout is not proof
of delivery: only the recorded merge, acceptance receipt and exact cleanup proof
permit completion. Historical proof survives installed-helper upgrades and
removal of temporary folders. Cancellation fences dispatch; proven worker exits
release active slots, while retained trees and unresolved ownership continue to
consume capacity until their owning cleanup proves absence.
If intake stopped before acquiring a lease and the Worktree owner has removed
the exact untouched outer, cancellation reconciliation releases its project
claim and admission slot. Historical outer metadata alone does not retain them.
The reconciliation verifies path, registration, branch, manifest and coordination
record absence; it does not delete recovery work or release an execution lease.

Execution-basis and validation-plan fingerprints use the execution producer's
canonical JSON digest during current and historical checks. Aggregate operation
and review receipts retain their own serialization contract. Changed basis or
plan content still blocks acceptance; never rewrite frozen evidence to repair a
consumer mismatch.

## Required final delivery message

When local implementation finishes but automatic delivery remains pending,
include the shared response's `user_message` in the final user response. Preserve
its original project path, source branch, retained checkout, actual blocker and
owner-specific `next_action`. Status is observational; neither reporting nor a
continuation hint mutates the source or grants permission to discard dirt.

| Observed evidence | Final message |
| --- | --- |
| Outer complete, promotion not performed | Changes remain in the outer; original-branch delivery is pending. |
| Validated candidate already promoted | Changes reached the original branch; finalization or cleanup is pending. |
| Promotion proof unavailable or inconsistent | Verification or recovery is pending; do not claim delivery or non-delivery. |
| Promotion and cleanup verified | Delivery is complete; omit the continuation command. |

For pending delivery, state that the original checkout must be clean and show
**only the owning workflow's command**, using the actual original project path:

```text
$implement continue "<original-project-folder>"
```

or, for an SDLC objective:

```text
$sdlc-start continue "<original-project-folder>"
```

The path is optional when invoked from the original project folder. A crash after
promotion must not be described as changes still awaiting promotion. Error
responses retain their original code and blocker alongside delivery guidance.

## Final gate records

Before finish, create one owner-private JSON report per gate and target under
`<run>/aggregation/reports/`. Each report has exactly `schema` equal to
`workflow/gate-review-v2`, `gate` (`alignment`, `uat` or `docs`), `head`,
`outcome` (`passed` or `failed` for alignment/docs; the honest UAT state for
`uat`), boolean `source_read_only`, and a nonempty
bounded `evidence` list naming the actual checks and retained evidence. Use the
actual inner child head for inner reports and the exact candidate head for
aggregate reviews. All aggregate reports require `source_read_only: true`:
runtime actions still need their existing explicit authority and resource lease.
Keep source unchanged; findings return to correction.

These reports are coordinator reviews, not executed-test receipts. Cite actual
UAT targets and distinguish historical child observations from candidate tests;
never relabel inner UAT as execution against the candidate. Run the acceptance
requested by the accepted objective at a ready scope; report missing optional
prerequisites as pending without blocking eligible delivery.
The combined evaluator and executable qualification owner retain their separate
machine-produced receipts. Only a claimed UAT PASS requires the executed
aggregate-stage OBJECTIVE qualification receipt; pending and not_requested do not.

Create the private gate input with exactly `target` (the current aggregation
identity), `inner` and `aggregate` maps each containing all three gates as
`{path, sha256}` references, and `qualifications` (the applicable producer
references, empty unless the aggregate UAT report claims passed). Paths are relative
to the run and digests use the shared canonical JSON digest. Call private intake
`aggregate-record-gates --run-dir <run> --candidate <gate-input.json>`.
It validates ownership, report targets, qualification coverage and unchanged
candidate identity, then preserves an immutable receipt. An identical replay is
idempotent; changed results cannot replace it. A later failed observation may
append one separate immutable failure receipt after a passed review, retaining
the original inner references and all earlier proof. Failure takes precedence;
a new passing review cannot erase it. Failed reviews also persist and
admit the fresh `aggregate-correct` execution path. Missing, failed or changed
gates block finish. Lease release uses the actual inner references; delivery
retains the distinct aggregate checks, reviews and qualification proof through
cleanup and later installed-source upgrades.

Qualification stages keep separate receipt indexes but share trial provenance.
A failed or intervened trial at either stage requires a fresh reset and trial
before qualification can pass at the other stage.

## Working on the aggregate

The original branch may advance while projects run. Commit and publication owners
share the source mutation lock and respect pending integration reservations.
Commit-push uses its exact-head publication helper even for an already-clean
branch. Branch retirement is blocked while an admitted child still depends on it.
Publication remains an explicit separate user request; local aggregation does
not imply permission to push, open a PR, merge or delete a branch.

Git worktrees have separate working files, indexes and HEADs, but share objects,
refs and repository configuration. Pushing the aggregate publishes only commits
reachable from that branch. Unfinished child commits need a separate authorized
backup/publication policy. Keep the aggregate branch until its children deliver.

See the [portable architecture presentation](../../docs/parallel-project-workflow.html).
