# Testing

Load this reference when creating or standardizing tests, pytest configuration,
Makefiles or CI. Keep the project manager and lock authority already selected.

## Choose The Smallest Meaningful Test

Test observable behavior, using the lowest layer that proves it. A real temporary
file does not automatically make a test integration. A CLI parser defect usually
belongs in unit tests; a complete installed CLI workflow may need E2E.

| Directory | Purpose | Dependencies |
| --- | --- | --- |
| `tests/unit/` | Focused logic, validation, state transitions and boundary decisions | Real application logic; fake/mock external boundaries |
| `tests/integration/` | Collaboration between real components | Controlled local dependencies |
| `tests/contract/` (optional) | External API, SDK, schema, protocol or message assumptions | Offline schemas or sanitized representative examples by default |
| `tests/e2e/` (optional) | Complete user-visible workflows | Realistic, explicitly configured environment; can be local |

Do not create `component/`, `regression/` or `smoke/` as default layers.
Regression describes why a test exists. Smoke describes a small critical subset
and can include E2E or installed-package checks. Neither changes network access.
Keep existing project taxonomies unless their migration is requested.

Generate only meaningful tests and optional directories/targets. Do not generate
placeholder assertions, duplicate a unit test under integration, or add a higher
layer merely because it is easier to write. An in-process CLI `--help` test is
argument-handling coverage, not proof of a complete installed workflow.

## Behavior And Regression Design

Use Arrange, Act, Assert: prepare the minimal relevant state, perform one
meaningful operation, then assert observable output, state or failure.
For important behavior consider success, boundaries, invalid or missing input,
empty results, dependency failure, timeout/retry limits and reported regressions.
Assert external interactions when the interaction itself is the contract; avoid
freezing internal call order or replacing the application's call graph with mocks.

For a behavior-changing bug fix:

1. Add a test at the lowest layer that reproduces the actual defect.
2. Demonstrate failure before the repair when safe and practical.
3. Apply the repair and verify the same test passes.
4. Keep the regression while the behavior remains supported.

If failing-first proof is impractical, explain the reason and remaining evidence
gap. Do not manufacture a trivial test simply to satisfy the rule.

Use `pytest.mark.parametrize` for the same behavior over boundary values, input
formats or expected errors. Give cases readable IDs and use fresh mutable inputs;
avoid duplicated functions and unnecessary Cartesian products.

## Mocks And Contracts

Keep business logic and internal collaboration real when practical. Patch cloud
SDKs, HTTP and subprocess boundaries when testing decisions; patch where the code
looks up the object. Prefer `autospec=True` for concrete Python APIs where it is
compatible with the target. Prefer `tmp_path` for normal filesystem behavior.
Mock filesystem operations for decision-only tests or destructive, privileged,
platform-specific or expensive operations.

```python
from unittest.mock import patch


def test_status_reports_dependency_failure():
    with patch("example.status.client.fetch", autospec=True, side_effect=TimeoutError):
        result = read_status()
    assert result.available is False
    assert result.reason == "timeout"
```

Adapt examples to real project APIs. Offline contract tests need identified
provider schemas, SDK versions or representative sanitized responses. Do not
validate a mock solely against another invented mock. Record provenance and
refresh conditions; offline compatibility evidence does not prove live service
behavior. Live contract verification additionally needs `external` permission.

## Fixture Architecture

Keep root `tests/conftest.py` for small cross-suite fixtures and guardrails.
Place database/local-server fixtures in integration conftests, and workflow
resources and target preflights beside the E2E/external tests that need them.
Default to function scope, explicit dependencies and small deterministic data.
Use `yield` and reliable cleanup; register cleanup immediately after acquiring a
resource so a later setup failure cannot leak it. Use broader scopes only with
immutable sharing or deterministic per-test reset. Avoid autouse except for
intentional global guardrails.

Treat flaky tests as defects: investigate leaked state, ordering, clocks, random
seeds, threads/processes, resources and dependency availability. Use bounded
readiness conditions instead of sleeps. Automatic reruns are not the default fix.

## Pytest And Network Policy

Use the pyproject and conftest assets as the executable authority; do not copy a
second implementation here. New scaffolds use pytest 9.1.1 or newer below 10,
pytest-cov 7.1 below 8 and pytest-socket 0.8.1 below 0.9, with Python 3.11-3.13.
Keep strict config/markers, importlib imports, `strict_xfail = true`, branch
coverage and required socket-plugin loading. Existing projects need a scoped
compatibility review before upgrading or changing import mode.

The conftest derives layer markers from directory roots and rejects conflicts.
Register optional `contract`, `e2e` and `smoke` markers when those tests exist;
register every generated marker under strict markers. Root-level test modules
must move into the appropriate layer when adopting this layout.

Network access is separate from classification:

- Ordinary tests have `--disable-socket`; unit tests cannot opt out.
- `local_network` permits loopback TCP (`127.0.0.1`, `::1`) for controlled
  dependencies outside unit tests. It is never implied by integration or E2E.
- `external` tests require explicit `--run-external` and an overridden
  `external_target` fixture. Its side-effect-free preflight must validate the
  explicitly selected non-production environment and return its identity.
  Missing preflight fails closed; never infer a target from ambient credentials.
- External client fixtures must be function-scoped and depend on
  `external_network`, which enables sockets only after target validation.
  Authorized external tests have broad Python socket access; restrict their
  runner egress when the project needs a destination boundary.
- Do not use raw `enable_socket`/`allow_hosts`, `socket_enabled`, or global
  force-enable/allow-host flags. The conftest rejects these policy bypasses.
- Async profiles may need `--allow-unix-socket` for event-loop socket pairs.
  Add it deliberately and test that Internet sockets remain denied; it also
  permits Unix-domain services, so do not claim isolation from local daemons.

### Enforcement Limits

This guard prevents common accidental Python socket access, not arbitrary
outbound effects. Collection imports must be side-effect-free: the plugin
applies during test setup, not collection. In pytest-socket 0.8.1, allow-host
mode guards `connect`, not all `connect_ex`/UDP/DNS paths. Teardown hooks restore
socket state, so cleanup is not a guaranteed guarded phase. Existing socket
aliases, subprocesses and native clients can bypass interception. Use controlled
process/container networking where strict egress isolation is required. Keep
network fixtures within one permission domain; test lifecycle and worker order
before sharing resources or adding parallelism.

## Commands And CI

The Makefile owns local/CI selections:

| Target | Selection |
| --- | --- |
| `test`, `test-fast`, bare pytest | `not slow and not e2e and not external` |
| `test-full`, `coverage` | `not external`, including slow and local E2E |
| `test-unit`, `test-integration` | Layer directory with `-m "not external"` |
| `test-contract`, `test-e2e` (when present) | Matching directory with `-m "not external"` |
| `test-smoke` (when present) | `-m "smoke and not external"` |
| `test-external` (configured projects only) | `-m external --run-external` plus target preflight |
| `smoke-wheel` | Fresh wheel build and isolated installed-artifact verification |

Optional targets use the same locked sync/run prerequisites as the baseline.
Do not suppress pytest's no-tests-collected exit code. Default marker filtering
still imports modules; a marker cannot make collection-time effects safe.
An explicit file/node retains default filtering; use a matching `-m` to select
another lane, but external tests still require their independent opt-in.

PRs run lint, fast unit/integration/offline-contract tests and artifact smoke.
Default-branch pushes, release tags and manual runs also execute full non-external
branch coverage. Resolve `{{default_branch}}` from the actual repository (use
`main` for a new repository unless instructed otherwise). Generate live CI only
after a manual environment-scoped target/preflight contract exists; tags never
authorize it. Follow the project's required gates and runtime budget.

Keep `uv lock --check`, `uv sync --locked` and `uv run --locked`. Library
lower-bound checks remain isolated as described in `dependency-management.md`.
Add xdist only when measurements justify it, using `optimize-pytest`; verify
worker isolation and preserve a serial correctness command.

## Installed Artifact And Coverage

Materialize `assets/smoke-wheel.py.template` as `scripts/smoke-wheel.py` when
packaging/automation is selected. Populate `RESOURCES` with exact required
package-relative files, including selected systemd units/timers. The helper
builds an sdist and then its wheel in fresh extracted staging, avoiding stale
checkout build output. It uses an isolated environment with locked
runtime dependencies, and installs only that exact wheel with `uv pip --no-deps
--no-index`. This limited artifact installation is not a second dependency
workflow. Run outside the checkout; verify import origin, installed metadata,
resources and declared console-script help. CLI help must be side-effect-free.
Keep the existing `build` target for producing distributable output.

Use branch coverage to find important untested decisions, not to pursue 100%.
Set a threshold only from the project's baseline and risk. With pytest-cov 7,
subprocess coverage is opt-in: configure Coverage.py `patch = ["subprocess"]`
under `[tool.coverage.run]` when child-process coverage is required and test it.
Never infer child coverage from parent coverage. Specialized performance,
property-based and security tests need a concrete project risk or invariant.

## Official Sources

- [pytest import practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [Parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- [Temporary files](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
- [Flakiness](https://docs.pytest.org/en/stable/explanation/flaky.html)
- [pytest changelog](https://docs.pytest.org/en/stable/changelog.html)
- [Python mock](https://docs.python.org/3/library/unittest.mock.html)
- [pytest-socket 0.8.1](https://pypi.org/project/pytest-socket/0.8.1/)
- [pytest-cov subprocess coverage](https://pytest-cov.readthedocs.io/en/stable/subprocess-support.html)
- [Branch coverage](https://coverage.readthedocs.io/en/latest/branch.html)
- [PyPA build default sdist-to-wheel behavior](https://build.pypa.io/en/stable/)
