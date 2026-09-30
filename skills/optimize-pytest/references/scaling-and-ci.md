# Scaling Pytest And CI

Initialize the inspected `pytest_base_args` array from `safe-measurement.md`
before running these Bash command examples. Keep every command inside the
already-preflighted selection and runtime deadline.

Use this reference only after serial measurements and isolation evidence show
that execution architecture, selection, or CI distribution is the next
bottleneck.

## Contents

- Adoption gates
- pytest-xdist
- Targeted and affected-test feedback
- Test lanes
- Coverage
- CI sharding
- Performance governance
- When to consider Pants
- Official references

## Adoption Gates

Before adding a plugin or changing CI:

1. Confirm the plugin is absent or insufficient in current dependency metadata.
2. Check compatibility with the project's supported Python and pytest versions.
3. Add it through the project's locked development-dependency workflow only
   when the user requested dependency changes.
4. Test debugging, IDE, coverage, fixture, worker-resource, and final-gate
   behavior.
5. Keep the existing serial command available.
6. Update configuration, lockfiles, native commands, CI, docs, and changelog as
   one contract.

Never install a plugin merely to diagnose a suite during an implicit or
report-only invocation.

## Pytest-Xdist

Each xdist worker performs collection and executes its own subset of tests.
High-scope fixtures execute per worker unless the suite deliberately coordinates
an immutable once-per-run artifact.

Benchmark serial and bounded worker counts on the same selection:

```bash
"${pytest_python}" -m pytest "${pytest_base_args[@]}" -q -n 0 \
  -o "cache_dir=${perf_dir}/pytest-cache-n0" "${pytest_target}"
"${pytest_python}" -m pytest "${pytest_base_args[@]}" -q -n 2 --dist=worksteal \
  -o "cache_dir=${perf_dir}/pytest-cache-n2" "${pytest_target}"
"${pytest_python}" -m pytest "${pytest_base_args[@]}" -q -n 4 --dist=worksteal \
  -o "cache_dir=${perf_dir}/pytest-cache-n4" "${pytest_target}"
```

Evaluate `-n auto` only after bounded counts, isolation, and machine resource
limits are understood. Current xdist normally maps `auto` to physical CPU
cores and supports `logical` when its optional CPU-detection dependency is
available, but environment variables or project hooks can override the worker
count. Record the effective count.

Choose a distribution mode from suite structure:

- `load`: general dynamic scheduling and the default.
- `worksteal`: useful initial candidate when test durations vary materially.
- `loadscope`: keep tests from the same class or module together for
  high-scope fixture reuse.
- `loadfile`: keep each file on one worker.
- `loadgroup`: keep explicitly marked resource-sharing groups together.

Parallel-safety checks:

- allocate worker-specific databases, schemas, ports, queues, accounts, files,
  and external resource names
- use `worker_id` or equivalent worker identity explicitly
- prove cleanup after worker failure
- avoid one shared mutable database or global registry
- account for repeated collection, imports, and per-worker application startup
- avoid oversubscribing CPU, memory, storage, container, and CI job limits

For a genuinely immutable artifact that must be created once across local
workers, xdist documents a file-lock pattern. Use it only after validating the
shared temporary root, atomic artifact creation, failure recovery, and cleanup.

Do not place `-n auto` into global addopts until serial debugging, IDE behavior,
coverage, output capture, fixture ordering, and resource isolation have passed.

## Targeted And Affected-Test Feedback

Use the smallest relevant pytest selection during implementation:

```bash
feedback_cache_dir="${perf_dir:?initialize perf_dir first}/feedback-cache"

"${pytest_python}" -m pytest "${pytest_base_args[@]}" tests/path/test_module.py::test_name -q -x \
  -o "cache_dir=${feedback_cache_dir:?}"
"${pytest_python}" -m pytest "${pytest_base_args[@]}" "${pytest_target}" --lf --last-failed-no-failures=none -q -x \
  -o "cache_dir=${feedback_cache_dir:?}"
"${pytest_python}" -m pytest "${pytest_base_args[@]}" tests/path/to/package -q \
  -o "cache_dir=${feedback_cache_dir:?}"
```

`--last-failed-no-failures=none` prevents an empty cache from expanding execution.
On the tested pytest 9.1.1 baseline, an empty failure selection exits 5 (no tests);
report that no rerun occurred rather than hiding empty-selection failures.
Keep the explicit inspected target and selectors; collection can still execute
imports and hooks. `--lf` reruns failures recorded by pytest's cache. It is not changed-code
analysis and must not replace a broad gate. Set `feedback_cache_dir` to one
intentional task-owned cache directory retained across these feedback runs;
do not point it at a broad or unrelated directory.

### Pytest-Testmon

Testmon builds a dependency database from test execution and then selects tests
affected by changed Python code:

```bash
testmon_datafile="${perf_dir:?initialize perf_dir first}/testmondata"

TESTMON_DATAFILE="${testmon_datafile:?}" \
  "${pytest_python}" -m pytest "${pytest_base_args[@]}" --testmon-noselect \
  -o "cache_dir=${perf_dir:?}/pytest-cache-testmon" "${pytest_target}"
# After a relevant source change, deliberately intersect affected tests with
# the same safety selectors retained in pytest_base_args.
TESTMON_DATAFILE="${testmon_datafile:?}" \
  "${pytest_python}" -m pytest "${pytest_base_args[@]}" --testmon-forceselect -q \
  -o "cache_dir=${perf_dir:?}/pytest-cache-testmon" "${pytest_target}"
```

Important boundaries:

- the first data-building run must execute the complete applicable suite
- testmon does not track arbitrary static assets or external services
- environment, Python, and dependency variants need compatible data separation
- selectors such as `-m`, `-k`, `--lf`, or explicit node IDs normally force
  no-selection mode; the second command deliberately uses `--testmon-forceselect`
  to intersect affected tests with those selectors without removing safety
  exclusions; an empty intersection means no tests ran, not a passing gate
- coverage/debugger modes can disable testmon data collection
- `.testmondata` is state that needs an intentional local, cached, or ignored
  location; `TESTMON_DATAFILE` keeps evaluation state in the task-owned
  directory

Use testmon for feedback, not as the sole correctness gate. Run a broad or full
suite when changes affect shared conftest files, pytest plugins, dependencies,
runtime configuration, common libraries, environment handling, dynamic
imports, schemas, migrations, generated code, static fixtures, templates,
serialization formats, or broadly consumed public interfaces.

## Test Lanes

Distinguish behavior layers from execution properties, following
`python-project/references/testing.md` for new scaffolds:

- unit: focused behavior with isolated external boundaries
- integration: real collaboration or controlled local dependencies
- contract (optional): interface/schema compatibility, usually offline
- E2E (optional): complete user-visible workflow, potentially local

`slow`, `smoke`, `local_network` and `external` are cross-cutting properties.
Filesystem IO alone does not determine a layer. Do not introduce a physical
component or regression layer by default. Preserve an existing project's layout
unless migration is requested. Fast integration and offline contracts can run
on PRs; live tests always retain independent opt-in and target preflight.

During migration, do not define the fast lane as `-m unit` until existing tests
are classified. A safe transitional lane may exclude known slower categories,
with selected-node identity and count assertions ensuring unmarked tests remain visible.
Add a regression check or recorded collection evidence for every lane before
trusting marker expressions documented in prose.

Enable strict markers after all markers are registered and the migration has a
correctness check. Do not make an integration test "unit" to improve timing.

## Coverage

Measure coverage overhead separately. A common architecture is a fast local or
pull-request lane without duplicate report generation and one required full
coverage lane, but repository policy decides where coverage is mandatory.

Pytest-cov supports xdist and combines worker coverage when configured
correctly. Verify current pytest-cov subprocess behavior, especially across
major plugin versions, before relying on coverage of spawned processes.

Keep affected-test selection and the authoritative coverage gate distinct.

## CI Sharding

Pytest-xdist distributes within one machine. For multi-job CI, pytest-split can
partition tests using stored historical durations:

```bash
durations_path="${perf_dir:?initialize perf_dir first}/test-durations"

"${pytest_python}" -m pytest "${pytest_base_args[@]}" --store-durations \
  --durations-path="${durations_path:?}" \
  -o "cache_dir=${perf_dir:?}/pytest-cache-split" "${pytest_target}"
"${pytest_python}" -m pytest "${pytest_base_args[@]}" --splits 4 --group 1 \
  --durations-path="${durations_path:?}" \
  -o "cache_dir=${perf_dir:?}/pytest-cache-split" "${pytest_target}"
```

Run every group in CI. Keep evaluation data in the task-owned directory. For
an explicit CI adoption, treat the chosen duration file as a governed input:

- choose whether it is committed or restored from a compatible cache
- refresh it after major suite changes
- detect missing groups and stale duration data
- account for newly added or renamed tests
- choose a splitting algorithm compatible with ordering/randomization policy
- avoid multiplying xdist workers beyond each CI job's CPU allocation

Pytest-split's default duration-based chunks preserve more ordering but are
incompatible with random-order plugins unless all shards share a compatible
seed. Its `least_duration` algorithm can balance more evenly while preserving
relative rather than absolute ordering. Benchmark the algorithm against the
suite's order-independence and fixture model.

Substantive GitHub Actions changes should use `github-workflows` after this
skill defines selections, worker counts, artifacts, dependencies, and gates.

## Performance Governance

Store bounded CI evidence and track:

- collection and total wall time
- serial and parallel selections
- collected/deselected and outcome counts
- setup, call, and teardown contributors
- worker utilization and long-tail tests
- fastest and slowest shard duration
- rerun cost
- coverage and reporting overhead

Set thresholds from historical data on the same runner class. Start with
warnings and owner-visible trends. Enforce only after accounting for variance,
runner changes, dependency changes, and intended test growth.

Performance evidence must not include secrets, private endpoints, customer
data, full environment dumps, or unbounded logs.

## When To Consider Pants

Consider Pants only when a Python-heavy monorepo needs repository-wide
dependency analysis, changed-target selection, process caching, concurrency,
and test batching beyond what pytest plugins and CI sharding can maintain.

Treat Pants as a separate build-system adoption. Evaluate:

- target/dependency modeling cost
- third-party lockfile and generated-code behavior
- developer and IDE workflow
- remote/local cache ownership
- test batching versus high-scope fixture reuse
- debugging and output behavior
- migration and rollback plan

Pants supports Git-aware changed-target selection with direct or transitive
dependents, and runs Python tests as fine-grained targets. Its default
per-target pytest processes can repeat high-scope fixtures; batching
compatibility and optional xdist need deliberate configuration. Do not adopt
Pants to avoid fixing fixture, isolation, or test-classification problems.

## Official References

- [pytest-xdist distribution modes](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
- [pytest-xdist worker and once-per-run patterns](https://pytest-xdist.readthedocs.io/en/stable/how-to.html)
- [pytest-testmon](https://www.testmon.org/)
- [pytest-cov xdist support](https://pytest-cov.readthedocs.io/en/stable/xdist.html)
- [pytest-split](https://jerry-git.github.io/pytest-split/)
- [Pants changed-target selection](https://www.pantsbuild.org/stable/docs/using-pants/advanced-target-selection)
- [Pants Python test batching](https://www.pantsbuild.org/stable/docs/python/goals/test)
