# SDLC Evaluate

`sdlc-evaluate` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Evaluate observed product behavior against real-world acceptance criteria and
any planned end-to-end slice, using the requirements Live Experiment
Environment only when it is confirmed safe and allowed. For predefined runtime
operational criteria, it may use `$nebius-grafana-query` as a bounded structured
evidence provider only after one criterion has an exact measurement and grading
rule, candidate/control attribution, and non-Grafana provenance for one
matching Grafana-backed signal.

Every failed criterion also emits an immutable `failure-event-v1` through
`scripts/failure_contract.py`. The event binds expected and observed behavior,
the exact regression oracle, evidence digests, integration commit, and
requirements/design/plan fingerprints. A proven mechanical owner bypasses
troubleshooting; an ambiguous evaluation failure is classified into the
conditional diagnostic branch.

## Main Boundaries

- Do not ignore negative criteria.
- Do not mark pass without observable evidence.
- Do not overwrite validation or test evidence.
- Do not use production or unconfirmed environments for live experiments.
- Passive production telemetry is read-only evidence, not permission to execute
  workloads or grade functional behavior.
- A failed one-time Grafana readiness check disables observability for the
  evaluation run without setup, repair, or retry.
- Missing criterion fit or matching-signal provenance means zero Grafana calls;
  a required underspecified criterion is inconclusive with `SPEC_GAP`.
- Each provider invocation attempts at most one pre-admitted data query. A
  later query needs a new exact grade-changing question; query budgets are
  ceilings, not targets.
- The six-query fast and four-query deep allowances are cumulative across the
  run; deep evaluation queries may only resolve a named attribution, coverage,
  or dependency interpretation of a predefined gate.
- Provider rejection reasons map deterministically to the existing SDLC failure
  taxonomy instead of being treated as Grafana outages.
- Evaluation does not speculate about implementation or design. Failure to find
  an implementation bug remains unresolved until decisive evidence exists.

## Primary Inputs

- Feature design.
- Acceptance criteria.
- Validation evidence.
- Test evidence.
- Product type.
- Live Experiment Environment section from `docs/requirements.md`, when present.
- Predefined operational criterion ID, exact measurement, threshold, grading
  conditions, deployed target, candidate/control attribution and windows,
  coverage requirement, and evidenced Grafana-backed signal when observability
  is eligible.

## Output

- Evaluation evidence exists.
- Acceptance criteria are explicitly pass, fail, or inconclusive.
- Planned end-to-end slice observation is recorded or a blocker is classified.
- Every failed criterion has a normalized failure event and deterministic
  classifier disposition.
- State moves to `evaluated` only when every required criterion passes.

Canonical routing cases live in `evals/trigger-prompts.csv`; deterministic
workflow expectations live in the supplemental `evals/process-cases.md`.
