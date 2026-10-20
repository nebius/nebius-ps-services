# Accepted objective contract

Normalize only outcome, scope, constraints, acceptance_criteria, clarifications
and provenance (kind and source_sha256). Lists contain accepted statements;
outcome is one string. Provenance kind is direct_request, current_plan or
steering. Never persist the source turn. The coordinator supplies these fields
through the shared Worktree intake helper; a user never edits a managed prompt.

Private entry points are `worktree/scripts/workflow_intake.py --owner implement`
with accept, observe or continue. They are not public skill actions. Acceptance
requires an owner-private normalized JSON candidate. Shared transitions live in
`worktree/scripts/workflow_transition.py`; inspect its help for exact arguments.
State and result schemas reject obsolete predecessors without conversion.

Before preparing execution, derive a complete objective-impact claim through
Maintain Project Specs: each normalized statement occurrence maps to changed or
existing contract records, execution_only, or a justified non_contract outcome.
Use settle-objective with that claim. Requirements readiness alone is not an
execution basis. Missing specs are allowed only when no statement maps to specs.

Prepared publication resolves to the registered integration checkout; delivered
publication resolves to the delivered checkout. Expected HEAD and paired prior
digests remain mandatory. Worker gaps return to the coordinator for replanning.
