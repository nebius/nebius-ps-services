# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

## Manual Runtime Check

Test these prompts in a fresh Codex session where the source skill is installed
or discoverable:

- Run canonical rows `pytest-opt-positive-01` through
  `pytest-opt-positive-10`. They should load `optimize-pytest` and follow its
  static preflight, safe measurement, cumulative-cost, and comparability
  workflow.
- Run canonical rows `pytest-opt-negative-01` through
  `pytest-opt-negative-09`. They should route to scaffolding, troubleshooting,
  general review, production performance, project testing, or workflow skills.
  The paired context boundaries `pytest-opt-positive-09` versus
  `pytest-opt-negative-07` and `pytest-opt-positive-10` versus
  `pytest-opt-negative-09` must depend on explicit pytest-suite scope.
- Ambiguous prompts should stay report-only until the user clearly asks for
  changes.

Report implicit activation as observed only after this check. Otherwise report
routing readiness from metadata inspection and static validation.
