# Optimize Pytest Trigger Prompts

Use these examples when reviewing or tuning implicit invocation behavior.
Static examples do not prove runtime activation.

## Should Trigger

- Our pytest suite has grown to 5,000 tests and now takes 18 minutes. Measure
  collection, fixtures, test calls, and teardown, then rank safe improvements.
- Review our root `conftest.py` for cumulative fixture cost and tell me whether
  any autouse fixtures or scopes should change.
- Pytest collection is slow. Inspect plugin loading, imports, discovery, and
  parametrization before recommending a fix.
- Benchmark safe pytest-xdist worker counts and distribution modes for this
  existing suite without making parallelism the default yet.
- Design a fast pytest feedback lane plus a complete correctness and coverage
  gate for this large Python service.
- Use $optimize-pytest to apply the measured fixture and test-classification
  improvements, then prove like-for-like results.

## Should Not Trigger

- Scaffold a new Python CLI with pytest, Ruff, and unit/integration test
  directories. Use `python-project`.
- This test fails intermittently only in CI; find the root cause and fix it.
  Use `troubleshoot`.
- Review this implementation for bugs, maintainability, and missing tests. Use
  `code-review`.
- Profile this production API endpoint and optimize its database queries. Use
  the relevant application performance workflow.
- Run the tests for the feature I just implemented. Use the relevant project or
  SDLC test workflow.
- Rewrite our GitHub Actions workflow permissions and release jobs. Use
  `github-workflows`.

## Boundary Prompts

- "Why are tests slow?" should trigger only when repository context shows
  pytest or the user identifies pytest-suite performance. Otherwise ask for or
  inspect the test runner before selecting this skill.
- "Fix the flaky slow test" should route to `troubleshoot` when nondeterminism
  is the primary problem; use `optimize-pytest` only after the cause is stable
  and performance is the remaining objective.
- "Make CI faster" should use this skill for pytest selection, fixture,
  parallelism, and sharding analysis, then hand substantive workflow editing
  to `github-workflows`.

## Manual Runtime Check

Test these prompts in a fresh Codex session where the source skill is installed
or discoverable:

- Should-trigger prompts should load `optimize-pytest` and follow its static
  preflight, safe measurement, cumulative-cost, and comparability workflow.
- Should-not-trigger prompts should route to scaffolding, troubleshooting,
  general review, production performance, project testing, or workflow skills.
- Ambiguous prompts should stay report-only until the user clearly asks for
  changes.

Report implicit activation as observed only after this check. Otherwise report
trigger readiness from metadata inspection and static validation.
