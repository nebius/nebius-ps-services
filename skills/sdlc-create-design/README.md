# Create Design

`sdlc-create-design` is an Agentic SDLC authoring adapter to
`maintain-project-specs`. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Convert requirements and gathered context into evidence-backed architecture
and feature designs in the canonical managed region of `docs/design.md`. The adapter maps stable `REQ-*` blocks
to stable `FEAT-*` blocks, records selected and rejected design options, and
defines vertical end-to-end feature flow, layer boundaries, implementation,
validation, test, evaluation, rollout, and rollback boundaries before planning
starts.

Failure-driven redesign is narrower than initial design work. It requires a
classifier-validated admission record proving a system-contract defect,
reproducibility at the recorded commit, valid evaluator/environment, stable
requirements, and `proven` or `high_confidence` causation. Internal-only
reconsideration may proceed automatically; changes to public contracts, data
lifecycle, security, permissions, deployment scope, or external behavior
require durable human approval. Inconclusive diagnosis stops instead of
becoming redesign.

## Design Method

- Understand requirements, priorities, constraints, non-goals, and open
  questions before designing.
- Inspect the existing system, including README files, architecture docs,
  source files, tests, configs, interfaces, and nearby patterns.
- Use gathered context as the technology and vendor evidence source; route
  missing or unverifiable facts back to `sdlc-gather-context`.
- Compare the baseline/current approach, selected design, and a simpler or
  more conservative alternative when the decision is non-trivial or
  hard to reverse.
- For serial multi-layer application features, record the end-to-end feature
  flow, layer map, boundary contracts, and cross-layer validation path.

## Main Boundaries

- Do not create local execution plans.
- Do not implement code.
- Do not modify tests.
- Do not rewrite requirements.
- Do not use for non-SDLC design docs, ADRs, or `/plan` handoffs.
- Do not delete feature blocks without explicit requirement removal.
- Do not accept a spec gap, probable diagnosis, large private implementation,
  or failure to find a code bug as design admission.

## Primary Inputs

- `docs/requirements.md`.
- Feature context packs.
- Existing `docs/design.md` when present.
- Current codebase shape.
- Validated design admission and approval for failure-driven reconsideration.

## Output

- `docs/design.md` exists.
- Every P0 requirement maps to at least one feature.
- Every ready feature has selected and rejected options, implementation
  boundaries, vertical flow or layer map when applicable, validation, test,
  evaluation, rollout, rollback, and done criteria.
- Open design questions are explicit.
- An admitted design change preserves FEAT IDs and records a new fingerprint
  for immutable plan vN+1; reaffirmation records why and returns to
  classification without another design loop.
