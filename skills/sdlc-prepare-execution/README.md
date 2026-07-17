# Prepare SDLC Execution

`sdlc-prepare-execution` is an explicit-only Agentic SDLC phase installed with
the other repo-owned skills. It runs after a feature plan is locked and before
TDD.

It validates the plan's stable task graph, creates deterministic dependency
waves, and prepares one persistent integration branch/worktree under the
private SDLC run directory. Later implementation workers receive separate
branches and worktrees from that integration line; the project branch does not
advance again until the final evidence-gated fast-forward promotion.

The exact folder initialized by `sdlc-start` is an enforced monorepo boundary:
Git worktrees contain the full repository, but claims, worker cwd, and changes
must remain under that folder. The coordinator supports explicit interrupted
worker transfer, resource-free future-wave replanning, sensitive-content gates,
and a shared Agentic SDLC lease when running inside a managed outer worktree.

The phase never implements product behavior, pushes internal branches, or
force-cleans resources. Interrupted or unsafe resources remain recorded for
exact recovery.

The private helper exposes `prepare`, `seal-tdd`, `replan-future`,
`wave-prepare`, `task-start`, `task-recover`, `task-finish`, `wave-integrate`,
`wave-complete`, `seal-feature`, `promote`, `release-outer-lease`, and `status`.
It is an internal state-transition surface, not a public SDLC CLI.
