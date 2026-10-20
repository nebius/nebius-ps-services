# Accepted Objective Refinement

`maintain-project-specs` interprets normalized accepted objective revisions. The
SDLC requirements/design phase skills are adapters to its canonical publisher.
Initial intent, clarification answers and steering use the same root-user
statement extraction and reconciliation workflow. Global eligible root-user
intent maintenance remains independent of both coordinators. Worker messages report gaps and never publish canonical specs.

Use the newest accepted outcome, scope, constraints, acceptance criteria and
clarifications. Inspect discoverable repository facts and preserve existing
product truth unless explicit steering changes it. An omission alone does not
remove a requirement. Do not persist raw conversation turns.

Classify each material statement: behavior, actors, interfaces, constraints,
acceptance, verification, non-goals, assumptions and dependencies. Use stable
clarification IDs in private `clarifications.json`; material unanswered or reopened
questions block contract lock and planning. Nonmaterial uncertainty is an explicit
assumption. Answers become a new normalized accepted revision when they change
intent; contradictory answers reopen the same question.

`workflow/requirements-refinement-v1` binds the objective ID, revision and intent
digest to a complete extracted occurrence inventory and stable clarification
ledger. `workflow_intake.py --owner agentic-sdlc refinement-ready` verifies
requirements readiness without requiring design or granting execution authority.

After applicable design publication, the root supplies a
`workflow/objective-impact-claim-v1` to private `settle-objective`. Every occurrence
has exactly one disposition: `changed_contract`, `existing_contract`,
`execution_only`, or `non_contract`. Contract effects map canonical requirement and
design IDs. No-effect reasons are justified as workflow directive, duplicate or
clarification context. The shared validator derives retain/replan effects;
unchanged spec bytes alone do not establish no effect.

Canonical reads and publication use the registered integration scope during
prepared execution and the delivered checkout afterward, at the expected revision.
Absent impact does not create unnecessary specs. Eligible new contracts may use the
owner's draft-pair bootstrap; design stays draft until substantive decisions exist.

Impact attempts and execution bases are immutable. New material steering requires
safe replanning; a justified no-effect revision may retain plan bytes while issuing
a fresh objective-bound basis. Prior evidence is retained. Public status reports
bounded classifications and readable objective descriptions, never raw captured
turns, secrets or private customer data.
