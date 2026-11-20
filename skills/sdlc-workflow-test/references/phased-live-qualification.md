# Native phased three-tier qualification

Use the existing three-tier lifecycle, application and public SDLC coordinator.
The tester observes transitions and owns fixture/runtime operations. It never
authors the implementation program, accepts revisions, advances checkpoints,
completes slices, or substitutes synthetic state for native workflow behavior.

Every three-tier generation uses a phased program. Run two fresh sequential
generations when qualifying both planning origins: private
`prepare --planning-mode supplied`, then `prepare --planning-mode derived`.
These are fixture-owner selectors, not new public skill flags. Each generation
has its own immutable source package, catalog, baseline, reports and cleanup.
Preserve both reports; neither run can certify the other planning mode.

Preparation commits generic source obligations under `docs/contracts/`. Supplied
mode also includes explicit phase boundaries and exits. Derived mode has no phase
plan. The native coordinator inventories the entire source package, publishes
canonical requirements/design through their owner, proposes at least three
bounded slices across multiple phases, and accepts the exact reviewed manifest
and program. Supplied phases must remain exact; derived phases must explain
outcome, dependency and risk choices. Review the derived ordering semantically;
schema validation alone cannot prove a useful plan. Do not embed an answer program
in the derived fixture or let the tester generate one for the coordinator.
All six fixture acceptance IDs must remain required and originate from the
frozen requirements file. Merely listing that file as context cannot qualify.
The planning provenance must cite the frozen requirements and, in supplied mode,
the supplied phase document.

Normalize explicit `local-promoted` completion and retain the existing whole-
objective UAT request. Every slice executes its frozen checks, finishes promotion
and cleanup, receives owner implementation admission and local completion, and
records applicable phase exits. Keep the outer lease until all slices finish.
The first vertical slice must integrate the title domain function and API in
distinct tasks using the same immutable acceptance dependency image. Declare
deferred filtering only in the slice that resolves it before its own delivery.

After at least one slice completes and future slices remain:

1. Finish the current observation boundary, leave all writers/validators/runtime
   leases quiescent, and exit the owned coordinator normally. Preserve its run.
   Finish/exit the companion too before the interruption observation if needed.
2. The tester records `record-program-checkpoint --stage interrupted`. It proves
   all registered native launchers are absent and records actual completed and
   future slices. It creates no workflow state.
3. Launch a fresh native coordinator through the existing owned launcher and use
   public `$sdlc-start continue <project-folder>`. Before new work or revisions,
   record `record-program-checkpoint --stage continued`. The owner binding must
   change while run, accepted inputs and completed-slice evidence remain exact.
   The native coordinator executes the installed intake owner's direct command:
   `python3 -B <installed-skills>/worktree/scripts/workflow_intake.py --owner
   agentic-sdlc continue --project-root <logical-project-root>`, followed by an
   ordinary workflow status call. Do not wrap continuation in compound shell
   commands. Its successful native command event and a later naturally emitted
   same-run hook must correlate with the new coordinator. The checkpoint retains
   bounded source references and event identities, never prompts or commands.
   A new launcher PID or changed owner hash alone is insufficient.
4. Submit the declared test steering: split or otherwise refine remaining work
   into a changed accepted program while preserving completed slice definitions.
   Use normal intake, spec reconciliation, admission and objective settlement.
   Keep both program definitions and every earlier receipt. Then finish all
   remaining slices through the public workflow.

Phase results use `phase-result-v6` with nullable `execution_id`. Startup is
observed once. Requirements/context/design/steering are revision-scoped and use
null execution. Planning through local ship is slice-scoped; final UAT and its
read-only documentation assessment name the final slice. Current admission binds
the owner-selected execution and current objective/basis. Historical validation
uses its own accepted revision, immutable archive and completed slice.
Historical checks before slice completion retain the same named read-only
target purpose used for initial classifier and TDD observation.
Attempt numbers remain global per phase; supersession applies only within one revision
and execution. A later slice cannot supply a missing earlier observation.

Do not set lifecycle `promoted_sha` after intermediate slices. Record it only for
the final assembled application. Earlier promotions live in slice completion
receipts. Intermediate evaluation checks the slice's bounded behavior; the full
browser evaluation and all five non-GUI test classes run when the complete app
is ready. Preserve the four browser roles, API/PostgreSQL correlation, restart
on the same volume, aggregate checks, Implement companion, statistics and cleanup.

The semantic `three-tier-results-v5` manifest includes `program`, the exact
read-only `three_tier_program.qualify` observation. Final ingestion recomputes
planning origin, accepted inputs, all local completions, phase exits, full current
coverage, interruption/continuation and changed-plan ancestry. A caller-provided
PASS or a directory of plausible receipts is insufficient. This establishes
local native phased execution; it does not establish protected remote delivery.
