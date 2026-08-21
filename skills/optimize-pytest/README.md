# Optimize Pytest

`optimize-pytest` is an implicitly invokable skill for measuring, reviewing,
and safely improving pytest suite performance in existing Python applications.

It separates startup, collection, setup, call, and teardown costs; ranks
cumulative contributors; preserves test selection and outcomes; and treats
parallelism, affected-test selection, sharding, coverage, and build-system
changes as evidence-gated escalations.

## Files

- `SKILL.md`: runtime workflow, boundaries, guardrails, and output contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/safe-measurement.md`: safe measurement and comparison guidance.
- `references/optimization-playbook.md`: ordered diagnosis and remediation
  rubric.
- `references/scaling-and-ci.md`: xdist, testmon, lanes, sharding, coverage,
  governance, and Pants guidance.
- `evals/trigger-prompts.csv`: canonical should-trigger, should-not-trigger, and boundary
  examples.
- `evals/process-cases.md`: supplemental workflow and runtime-check cases.

## Boundaries

- Use `python-project` for Python project scaffolding.
- Use `troubleshoot` when failure, hangs, or flakiness are the primary problem.
- Use `code-review` for generic implementation review.
- Use `github-workflows` for substantive GitHub Actions changes after the test
  performance requirements are defined.
