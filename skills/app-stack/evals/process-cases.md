# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below to review `app-stack` routing precision and
the assertions here to review output quality. Static validation does not prove
runtime activation.

## Quality Scenarios

For canonical positive cases, verify that the result:

- classifies the application before naming products;
- states assumptions and decision-changing unknowns;
- recommends a simplest baseline;
- marks components `Required`, `Conditional`, `Deferred`, or `Rejected`;
- does not add queues, caches, workflows, streams, or Kubernetes without a
  requirement;
- considers a cohesive batteries-included server framework when integrated
  forms, authentication, admin, ORM, and migrations reduce the stack;
- provides a revisit trigger for conditional and deferred choices;
- distinguishes commands, schedules, workflows, and events;
- includes data ownership, security, reliability, observability, recovery, and
  operational ownership;
- verifies volatile vendor claims or marks them unverified;
- stays read-only for advice-only prompts;
- stops before unconfirmed live, production, destructive, credential, or paid
  external-service mutation;
- coordinates narrow specialist skills only when implementation is requested;
- routes undecided AI-specific layers through `ai-stack` while retaining the
  surrounding product-stack decision;
- returns a scoped stack decision to an active `design` workflow instead of
  recursively handing the full request back to `design`.
- emits only logical technology decisions in a scaffold handoff and leaves
  repository topology, paths, candidate sets, and per-file owners to
  `scaffold-project`.
- uses schema version 2, a closed component class, and a canonical technology
  name for every scaffold-handoff component.
- leaves non-frontend capability selections empty for the current executable
  scaffold contract and records those requirements as constraints until a
  specialist owns a closed binding.

## Manual Runtime Check

Exercise `app-stack-positive-01` through `app-stack-positive-10` and
`app-stack-negative-01` through `app-stack-negative-10` in a fresh Codex thread
where the source skill is installed or discoverable. Report implicit activation
as observed only when the target surface actually loads `app-stack`. Otherwise
report metadata and static eval readiness.
