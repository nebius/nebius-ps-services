# Start SDLC

Start with `$sdlc-start <objective>` or `$sdlc-start` for the current unambiguous
plan. The same session continues its explicit binding; a new session uses
`$sdlc-start continue [project-folder]`. Use `$sdlc-start status [project-folder]`
for read-only discovery. Help is read-only.

The coordinator persists normalized objectives and immutable execution bases,
keeps one accepted objective and its dependent feature queue, and routes existing
SDLC phases. Worktree owns Git transactions and fresh task worktrees. Root-owned
spec interpretation remains with maintain-project-specs. Global spec maintenance
is independent of workflow intake; no prompt capture is installed.

Accepted registration and initialized phase state are separate. After fresh
intake, the bound coordinator invokes the private `initialize_phase.py` helper
to create its first `sdlc-start` checkpoint and current state before observation
or routing. The helper validates native ownership and any managed outer lease.
Known hook audit files remain intact and do not count as completed phases.
Existing or partial phase history follows reconciliation; an active pointer
never justifies resetting evidence or counters.
Intake helpers, hooks and verifiers do not create these phase transitions.

Phase outputs such as `execution_prepared`, `tests_red` and `tests_ready` use
`current_phase` with root `status: running`. Keep the current state and its
immutable checkpoint consistent; the execution coordinator's status is separate.

Prepared phases use the registered integration checkout. Promotion retains
unchanged-source checks and managed outer-worktree ownership. Post-promotion
documentation drift starts a fresh corrective execution before automatic release,
or a fresh objective after terminal release, preserving completed execution.
Remote publication still requires its existing authorization and exact-commit checks.

Automatic objectives use one outer per project and the shared admission/capacity
registry. Start separate project sessions, then use `$sdlc-start status` or
`$sdlc-start continue <original-folder>` without remembering checkout names.
After final inner gates, the coordinator validates a candidate against the
current aggregate, promotes it under the shared source lock, and removes exact
owned resources. Durable completion proof remains after cleanup. Manually
managed outers keep their separately authorized Worktree handoff.
See the [automatic contract](../worktree/references/automatic-projects.md) and
[portable architecture presentation](../docs/parallel-project-workflow.html).

In verifier-owned disposable runs, hook Git reads validate the sealed Git
profile before observing project identity or staged content. Invalid profiles
fail closed; ordinary sessions retain their existing Git environment.

Secret screening permits bounded unquoted lookup/call expressions, including
CSRF DOM access, while retaining literal, ambiguous-input and provider-specific
credential checks across shell, patch and native write tools.

See [SKILL.md](SKILL.md), [state schema](references/state-schema.md) and the
[shared execution contract](../worktree/references/workflow-contract.md).

## Repair dispatch completion

The feature dispatch ceiling blocks new repair admissions. The last admitted
repair may finish under its existing authority and complete required
revalidation. Over-limit counts and missing dispatch or cursor authority remain
blocked. Successive classifications retain earlier invalidations, and the
current successful repair must rerun their ordered gates before resolution.

## Execution statistics

Before, during and terminal summaries report named architecture, known tasks,
source/revision references, measured intervals and explicit gaps. Existing
`status [project-folder]` reads the same saved-report projection without writes.
See [statistics](../worktree/references/statistics.md). No ETA is produced.

Automatic admission allows unrelated sibling dirt when the selected project,
canonical specs, effective instructions and declared shared inputs are clean.
The coordinator freezes committed dependencies before creating the outer; no
uncommitted content is copied. Manual worktrees retain their full-clean rule.

## When delivery waits

The final response names the original branch, retained work and actual blocker.
After making the whole original checkout clean, resume from the original project:

```text
$sdlc-start continue "<original-project-folder>"
```

A promotion that already happened is reported as finalization or cleanup pending;
uncertain proof is reported as verification or recovery pending. Completed delivery
has no continuation command. The workflow never commits or stashes unrelated work.

UAT follows the [shared optional policy](../worktree/references/optional-uat.md).
Requested campaigns wait for a meaningful ready scope. An existing test environment
uses its project harness directly; container/Nebius campaigns require explicit
skill selection. Missing optional prerequisites leave a handoff without blocking
implementation or eligible publication. Actual failures and cleanup remain owned.
