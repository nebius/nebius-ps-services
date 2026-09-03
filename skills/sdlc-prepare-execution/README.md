# Prepare SDLC Execution

`sdlc-prepare-execution` is an explicit-only Agentic SDLC phase installed with
the other repo-owned skills. It runs after a feature plan is locked and before
TDD.

It validates the plan's stable task graph, creates deterministic dependency
waves, and prepares one persistent integration branch/worktree under the
private SDLC run directory. Later implementation workers receive separate
branches and worktrees from that integration line; the project branch does not
advance again until the final evidence-gated fast-forward promotion.

Preparation writes coordinator v7. In unmanaged mode it resolves the actual
`origin` default and creates the deterministic run promotion branch when the
clean checkout is still on that default. In a managed `worktree` child it uses
the exact local child branch and `HEAD` without fetching or resolving a remote
default.
Task Implementer persistent lanes are a separate peer workflow domain and are
rejected before Agentic coordinator, lease, promotion, or resource mutation.

The exact folder initialized by `sdlc-start` is an enforced monorepo boundary:
Git worktrees contain the full repository, but claims, worker cwd, and changes
must remain under that folder. The coordinator supports explicit interrupted
worker transfer, resource-free future-wave replanning, sensitive-content gates,
and a shared Agentic SDLC lease when running inside a managed outer worktree.
Corrective replanning holds the transition lock, preserves full definitions and
digests for every active or completed task, and appends only resource-free
future waves. Sealed, promoted, or completed execution cannot be reopened.

The shared lease is schema v4 with `active` and terminal `released` states.
Promotion persists Git, lease, local interop, then coordinator state in that
order; resume reconciles exact durable identity across every boundary. Release
requires a clean exact outer head and absent internal resources and leaves a
receipt until the outer worktree is removed.

The phase never implements product behavior, pushes internal branches, or
force-cleans resources. Interrupted or unsafe resources remain recorded for
exact recovery.

The private helper exposes `prepare`, `seal-tdd`, `replan-future`,
`wave-prepare`, `batch-advance`, `task-arm`, `task-start`, `task-heartbeat`,
`task-watch`, `task-requeue`, `task-recover`, `task-finish`, `wave-integrate`,
`wave-complete`, `seal-feature`, `promote`, `release-outer-lease`,
`complete-outer-integration`, and `status`.
It is an internal state-transition surface, not a public SDLC CLI.

An armed task may return to the queue only after the coordinator has stopped
the worker process group and supplies the exact recorded dispatch timestamp.
The task must still be attempt zero with no session claim, at the clean assigned
base and with no changed paths. Pre-start moved `HEAD`, out-of-claim paths,
gitlinks, or tracked symlinks are scope violations; only allowed in-claim dirt
is a pre-start mutation.

Before it commits, `task-finish` persists an exact assignment/tree/message/
evidence intent. A retry adopts only the matching clean direct-child commit and
reuses only an exact matching result, which closes both commit-to-result and
result-to-task-state crash windows without accepting worker-created history.

For a corrective assignment, `task-finish --oracle-evidence-json` must name
the assignment's exact regression oracle, a `passed` outcome, and an evidence
reference. The helper binds that proof to the diagnosis and the
coordinator-created task commit in
`worker-result-v5`; missing or mismatched proof fails closed. Assignment v4
also binds the accepted root-intent digest and canonical project-spec receipt;
worker results carry typed `spec_gaps` for root-coordinator reconciliation.
Worker path inventories disable Git rename folding so protected source paths
cannot disappear behind an allowed destination, and all gap text uses the same
sensitive-evidence scanner as validation and commit metadata.
