# Pytest Optimization Playbook

Use this reference after a representative baseline identifies the expensive
phase or cumulative contributor. Review in this order and stop when the
smallest safe change meets the objective.

## Contents

- Rank cumulative cost
- Autouse fixtures
- Fixture scope and shared state
- Test classification and boundaries
- Database and application startup
- Import-time work and collection
- Parametrization and test data
- Sleeps, polling, subprocesses, and algorithms
- Retries, coverage, and reporting
- Lane design and regression controls
- Anti-optimizations

## Rank Cumulative Cost

Prioritize total contribution, not only the longest individual test:

```text
estimated cumulative cost =
    observed phase duration per invocation
    x representative invocation count
```

Also consider parallel duplication, cache misses, fixture reuse, failure
frequency, and confidence in the measurement. A 50-millisecond fixture used by
5,000 tests can matter more than one two-second test.

For every recommendation, record:

- evidence and affected phase
- estimated cumulative contribution
- expected benefit
- correctness and flakiness risk
- implementation scope
- proof required before acceptance

## Autouse Fixtures

Inspect every `autouse=True` fixture, starting with the highest-level
`conftest.py`.

Look for:

- application or dependency-container creation
- configuration, secret, or catalog loading
- database connections, migrations, or schema creation
- event loops, clients, containers, processes, or sockets
- global registry resets or large object graphs
- extensive patching or temporary directory construction

Do not remove an autouse fixture merely because it runs often. Lightweight
safety behavior, such as blocking accidental unit-test network access, may be
valuable precisely because it applies everywhere.

Prefer explicit fixture dependencies when only a subset of tests needs the
behavior. If an autouse safety fixture stays, keep its fast no-op or patch path
small and deterministic.

## Fixture Scope And Shared State

Pytest fixtures default to function scope. Consider:

- function scope for mutable state requiring per-test isolation
- class or module scope for safely shared resources within that boundary
- package or session scope for immutable resources or services with
  deterministic per-test isolation
- factory fixtures that share expensive immutable initialization but return
  cheap isolated instances
- database transactions or savepoints that roll back each test
- `tmp_path_factory` for expensive immutable session artifacts

Never widen scope solely for speed. First separate expensive resource creation
from mutable state reset. Prove:

- no order dependency
- no state leakage after failures
- deterministic cleanup
- safe behavior under repeated and randomized execution
- safe behavior under the intended parallel worker model

With pytest-xdist, a high-scope fixture normally runs once per worker, not once
globally. Account for that multiplication before widening scope.

## Test Classification And Boundaries

A practical unit lane should normally avoid:

- real cloud, network, DNS, or shared-service access
- container or database-server startup
- CLI process spawning when the underlying Python boundary can be tested
  directly
- migrations, production-sized models, or large datasets
- real clocks, sleeps, long polling, or production password-hashing cost
- shared queues, accounts, ports, schemas, or files

Preserve valuable cross-boundary tests, but classify them honestly as
component, integration, contract, end-to-end, or slow tests.

For unit tests, fake stable architectural boundaries:

- HTTP transport or API client
- persistence repository or port
- injected clock or scheduler
- process adapter beneath the CLI
- queue or broker port
- filesystem boundary using minimal temporary data

Avoid mocking internal implementation details merely to make tests faster.

## Database And Application Startup

Review whether tests repeatedly:

- create or drop a database
- apply all migrations or ORM metadata
- load large seed datasets
- truncate every table
- start a server or container
- serialize access through one shared schema

Prefer, when the application's isolation model supports it:

1. Create the database/schema once per session or worker.
2. Allocate worker-specific databases, schemas, ports, queues, or accounts.
3. Start a transaction or savepoint per test.
4. Roll back each test instead of rebuilding the schema.
5. Keep separate tests that explicitly validate migrations.

Review repeated application factories the same way. Separate immutable route,
schema, and dependency metadata from request-specific mutable state. Lazily
initialize optional production integrations in test configuration.

Do not share application instances until global caches, dependency overrides,
registries, background tasks, event loops, and clients have deterministic reset
or ownership.

## Import-Time Work And Collection

Pytest imports test modules during collection. Search for module-level:

- large JSON, YAML, CSV, manifest, or model loading
- cloud/service discovery or database connection
- application construction
- subprocess execution
- expensive optional framework imports
- generated case matrices
- expensive `pytest_generate_tests` work

Move expensive runtime setup into fixtures or lazy functions. Use indirect
parametrization when expensive resource creation should occur during test setup
rather than collection.

Limit discovery to actual test roots with `testpaths` or explicit selections.
Add `norecursedirs`, `--ignore`, or discovery-pattern changes only when static
evidence shows pytest is scanning irrelevant content. Do not cargo-cult a
directory list into projects whose `testpaths` already bounds discovery.

## Parametrization And Test Data

Review stacked parametrization, parametrized fixtures, dynamic datasets, and
generated cases for unintended Cartesian products.

Retain:

- boundary values
- meaningful equivalence classes
- known regressions
- distinct execution paths
- contractual compatibility combinations

Move exhaustive matrices into a slower lane when they remain valuable. Do not
delete cases simply to reduce the test count, and never compare runtimes as
equivalent if the collected count changes.

Keep factories minimal by default. Opt into large object graphs, many database
rows, production-sized manifests, binary files, or workflow histories only
when the behavior requires them. Reuse expensive immutable artifacts through a
safe high-scope fixture or `tmp_path_factory`.

## Sleeps, Polling, Subprocesses, And Algorithms

Search test and fixture code for real sleeps and polling:

```bash
rg -n 'time\.sleep|asyncio\.sleep|sleep\(' tests
```

Prefer injected clocks, controllable schedulers, immediately resolved futures,
event-driven synchronization, and assertions over calculated retry delays.
Keep real bounded polling for integration behavior that genuinely requires it.

Review subprocess-heavy tests for repeated interpreter startup, package
discovery, shell validation, Git repository creation, process timeout, and
cleanup. Test Python functions directly for the broad matrix, retaining a
smaller number of CLI/process integration tests.

Use reduced test parameters or injected lightweight implementations for
production-strength hashing, key derivation, compression, image processing,
large serialization, or model inference. Retain focused integration tests for
the real production configuration.

## Retries, Coverage, And Reporting

Automatic reruns can multiply both runtime and uncertainty. Treat reruns as
temporary diagnostic mitigation, not a performance solution. Track:

- pytest rerun plugins
- CI job retries
- test-level retry decorators
- long polling timeouts
- broad exception handling followed by retry

Measure coverage, JUnit, tracing, and other reports independently. Generate an
expensive report once per required lane rather than duplicating it across
workers or jobs. Preserve required coverage policy even if the local fast loop
uses a non-coverage command.

## Lane Design And Regression Controls

Introduce markers or directories only with a migration plan:

- register every marker
- classify existing tests honestly
- verify every lane selects the intended tests
- enable strict markers after marker registration and migration are complete
- keep a complete correctness gate

Useful lanes can include:

- targeted changed module or exact node ID
- fast unit lane
- component/integration/contract lane
- slow/end-to-end lane
- complete correctness and coverage lane
- scheduled external, randomized-order, compatibility, or performance lane

Choose internal targets from the measured baseline and infrastructure. Track:

- startup and collection time
- serial and parallel wall time
- test and deselection count
- setup, call, and teardown contributors
- worker utilization and long-tail tests
- shard balance
- rerun cost
- coverage/reporting overhead

Warn before enforcing thresholds. Version threshold data and explain its
environment and selection.

## Anti-Optimizations

Reject changes that improve a number while degrading the suite:

- combining independent tests into one large test
- dropping cases without branch or contract analysis
- weakening assertions or error checks
- skipping or xfail-marking slow failures to make the lane green
- hiding flakes behind permanent retries
- globally serializing a suite to avoid fixing shared-state conflicts
- widening fixture scope without reset proof
- labeling integration behavior as unit behavior
- treating `--lf`, testmon, markers, or shards as the final correctness gate
- placing `-n auto` in global addopts before proving parallel safety

Optimize shared setup, stable external boundaries, classification, selection,
execution architecture, and feedback routing while keeping tests small and
diagnosable.

## Official References

- [pytest fixture scopes](https://docs.pytest.org/en/stable/how-to/fixtures.html#scope-sharing-fixtures-across-classes-modules-packages-or-session)
- [pytest autouse fixtures](https://docs.pytest.org/en/stable/reference/fixtures.html#autouse-fixtures-fixtures-you-don-t-have-to-request)
- [pytest indirect parametrization](https://docs.pytest.org/en/stable/example/parametrize.html#indirect-parametrization)
- [pytest collection controls](https://docs.pytest.org/en/stable/example/pythoncollection.html)
- [pytest markers](https://docs.pytest.org/en/stable/how-to/mark.html)
- [pytest temporary paths](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
