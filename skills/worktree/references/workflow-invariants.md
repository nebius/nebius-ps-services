# Shared workflow invariants

These rules apply to Implement and every Agentic SDLC phase. They do not
start a workflow, replace its coordinator or grant additional authority.

## Ownership and current inputs

- Worktree owns local execution, Git transactions, resource admission and
  recovery. Implement and Agentic SDLC retain their separate coordinators.
  Never nest coordinators or reconstruct a private helper's authority.
- Use the validated native session/run binding and selected project. An active
  pointer, sole existing run, PID, timestamp or worktree lock is not ownership.
- Read applicable instructions and the current complete checkpoint before
  changing phase or evidence. Preserve accepted scope and unrelated dirty work.
- Before execution preparation, bind evidence to the accepted objective and
  applicable spec receipt. After preparation, bind it to the current immutable
  basis, assignment, exact registered checkout and required Git identity.
- `maintain-project-specs` owns canonical interpretation and paired publication.
  SDLC authoring adapters retain their respective responsibilities. Workers
  return typed spec gaps; they never publish canonical specs.

## Evidence and effects

- Keep private workflow state outside committed project files. Never store raw
  turns, secrets, credentials or private endpoints in workflow artifacts.
- Preserve immutable plans, assignments, results, failed attempts and history.
  Material changes require their owner's fenced revision and fresh basis.
- Recheck current authority and required identity at every effect boundary.
  Previously loaded instructions or passing evidence do not waive those checks.
- Observe writer termination independently before result admission, recovery,
  integration or cleanup. Respect exclusive runtime leases and native capacity.
- Preserve workflow-specific commit ownership: Implement workers commit their
  assigned code; SDLC commits remain coordinator-owned. Retain deterministic
  integration, required combined checks and exact-resource cleanup. Do not reset,
  stash, force-remove or adopt unknown resources to obtain progress.
- Classify unexpected failures before repair or retry. Preserve the original
  failure, affected gates and budgets; use the existing recovery owner.
- For runtime access during SDLC worker validation recovery, recheck the active
  counted same-assignment repair, fresh worker and prior termination through
  the recovery owner. Observation grants no phase change or runtime lease.
- TDD, test execution, code review, evaluation and UAT remain distinct evidence.
  Missing, stale, skipped or intervened evidence is never a passing result.
- UAT is optional under [its policy](optional-uat.md). Pending optional
  prerequisites do not block otherwise eligible implementation or publication.
- Local completion grants no remote publication or merge authority. Preserve
  the accepted completion policy and protected delivery owners.

## Shared skill calls

- Context gathering uses local evidence first and calls research only for
  bounded unresolved questions; its SDLC adapter owns the context pack.
- SDLC design uses design and specialists as advisors. The design adapter and
  canonical publisher retain authoring ownership; no nested planner starts.
- Validation uses report-only code review and check-only lint. Findings return
  to the existing root/classifier instead of creating a second repair loop.
- Alignment returns changed paths and affected gates for owner revalidation.
  Diagnosis returns to the classifier; protected delivery keeps its owner.

## Conditional detail and reuse

- Read [the execution contract](workflow-contract.md) before intake,
  continuation, execution planning, worker dispatch/result admission,
  integration, promotion, cleanup or execution recovery.
- Read [document delivery](document-delivery.md) for accepted source manifests,
  programs, SLICE identities, source coverage, phase barriers, delivery
  admission, phase exit or protected publication/completion policy.
- Load each phase's other required references when their stated trigger applies.
  Already-loaded unchanged instructions may be reused within the same session.
  After compaction or relevant source changes, reload the needed instructions.
- Reuse instructions only; never cache mutable authority or substitute memory
  for current checkpoint, basis, evidence, ownership or effect-boundary checks.
