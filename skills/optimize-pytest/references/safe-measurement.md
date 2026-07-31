# Safe Pytest Measurement

Use this reference before executing a pytest timing, collection, fixture,
plugin, import, or profiler command.

## Contents

- Safety and representativeness
- Phase model
- Preflight
- Temporary artifacts and cache
- Runtime deadline
- Baseline commands
- Targeted fixture and profiler diagnostics
- Repeated sampling
- Like-for-like comparison
- Interpreting results
- Official references

## Safety And Representativeness

Pytest executes project and third-party Python code. Even `--collect-only`
imports test modules and `conftest.py` files and runs collection hooks. Treat
collection as code execution, not as a side-effect-free inventory command.

Before executing:

1. Read applicable repository instructions and inspect the current Git status.
2. Inspect pytest configuration, testpaths, addopts, plugin hooks, fixtures,
   marker registration, dependency metadata, and native command expansion.
3. Identify imports, hooks, or tests that can access networks, subprocesses,
   containers, databases, cloud APIs, shared files, sockets, clocks, or
   credentials.
4. Reuse an already-installed project interpreter. If pytest or a required
   plugin is absent, report it; do not install it merely to collect a baseline.
5. Choose the narrowest representative selection and a bounded runtime.
6. Capture Git status before and after execution so unplanned project writes
   are visible.

Do not use a Make, task-runner, or package-manager target for timing until its
prerequisites are expanded. A target named `test` may create a virtual
environment, install or upgrade dependencies, generate code, or build assets.

Default to terminal-only output. When a file is necessary, create a fresh,
task-owned directory under the system temporary directory, validate the
resolved path, and keep every measurement artifact under it. Do not reuse an
existing `--basetemp` path: pytest clears that directory before use.

`PYTHONDONTWRITEBYTECODE=1` can reduce bytecode writes but does not prevent test
code, plugins, caches, or application imports from writing other files.

## Phase Model

Classify costs without claiming more precision than the tool provides:

- **Startup:** interpreter startup, pytest startup, plugin loading, and
  configuration parsing.
- **Collection:** importing test modules, loading conftest files, discovering
  tests, expanding parametrization, and collection hooks.
- **Setup:** fixtures, databases, application factories, temporary resources,
  containers, clients, and dependency wiring.
- **Call:** the test body and system-under-test execution.
- **Teardown:** fixture finalizers, rollback, process shutdown, container
  cleanup, and file deletion.

A timed `--collect-only` run combines startup and collection. Import-time and
plugin traces help attribute that total, but they do not create a perfectly
isolated phase boundary. Pytest duration reporting exposes setup, call, and
teardown reports for executed tests. JUnit, coverage, and profilers add
instrumentation overhead and are separate diagnostic variants.

## Preflight

Resolve and record:

- repository root and source/diff identity
- clean or dirty state and whether files are changing concurrently
- exact existing interpreter path
- Python, pytest, and active plugin versions
- pytest rootdir and configuration file
- configured testpaths, addopts, markers, warning filters, and cache behavior
- requested selection and expected collection/outcome counts
- warm-cache or cold-cache policy
- CPU, memory, storage, container, and external-service constraints relevant
  to the comparison

Do not use a changing or concurrently edited checkout as performance evidence.
It may still be inspected statically, but timing claims must wait for a stable
source state.

## Temporary Artifacts And Cache

Set `pytest_python` to the absolute path of the already-installed project
interpreter and `repo_root` to the absolute repository root discovered during
preflight. Then create one fresh task-owned directory before using the command
templates:

```bash
: "${pytest_python:?set pytest_python to the existing project interpreter}"
: "${repo_root:?set repo_root to the repository root}"

case "${pytest_python}" in
  /*)
    ;;
  *)
    printf 'pytest interpreter path is not absolute\n' >&2
    exit 1
    ;;
esac

case "${repo_root}" in
  /*)
    ;;
  *)
    printf 'repository root is not absolute\n' >&2
    exit 1
    ;;
esac

if [ ! -x "${pytest_python}" ] || [ ! -d "${repo_root}" ]; then
  printf 'pytest interpreter or repository root is unavailable\n' >&2
  exit 1
fi

pytest_tmp_root=$(
  "${pytest_python}" - <<'PY'
import tempfile
from pathlib import Path

print(Path(tempfile.gettempdir()).resolve(strict=True))
PY
)

repo_root_resolved=$(
  "${pytest_python}" - "${repo_root}" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).resolve(strict=True))
PY
)

case "${pytest_tmp_root}" in
  /*)
    ;;
  *)
    printf 'temporary root is not absolute: %s\n' "${pytest_tmp_root}" >&2
    exit 1
    ;;
esac

if [ "${pytest_tmp_root}" = "/" ] ||
  [ "${repo_root_resolved}" = "/" ] ||
  [ ! -d "${pytest_tmp_root}" ] ||
  [ ! -w "${pytest_tmp_root}" ]; then
  printf 'unsafe temporary or repository root\n' >&2
  exit 1
fi

case "${pytest_tmp_root}/" in
  "${repo_root_resolved}/"*)
    printf 'temporary root is inside the repository\n' >&2
    exit 1
    ;;
esac

perf_dir=$(mktemp -d "${pytest_tmp_root}/optimize-pytest.XXXXXX")

case "${perf_dir}" in
  "${pytest_tmp_root}"/optimize-pytest.*)
    ;;
  *)
    printf 'unsafe pytest performance directory: %s\n' "${perf_dir}" >&2
    exit 1
    ;;
esac

if [ ! -d "${perf_dir}" ] ||
  [ -L "${perf_dir}" ] ||
  [ ! -O "${perf_dir}" ]; then
  printf 'invalid pytest performance directory: %s\n' "${perf_dir}" >&2
  exit 1
fi
```

Stop if creation or validation fails. Keep profiles, import traces, JUnit XML,
pytest cache, and other generated evidence under `perf_dir`. Do not point
`--basetemp` at `perf_dir` itself; use a new child such as
`"${perf_dir}/pytest-basetemp"` only when a test selection needs it.

The examples isolate pytest's cache with:

```bash
-o "cache_dir=${perf_dir}/pytest-cache"
```

Use that option consistently in comparisons. Do not use an isolated empty
cache for `--lf` or another workflow whose purpose depends on existing cache
state. When warm-cache behavior is part of the measurement, prepare equivalent
task-owned cache copies and document the policy.

## Runtime Deadline

`time` records elapsed time; it does not limit execution. Before running a
measurement, choose a real deadline mechanism already available in the
environment:

- an executor or controller deadline that terminates the pytest process tree
- a repository-approved process timeout utility
- an already-installed pytest timeout plugin for test execution, with a
  separate process deadline for startup and collection
- an existing CI job timeout for a deliberately scoped CI measurement

Record the deadline and termination behavior. Do not install a timeout tool or
plugin merely to measure the suite. If no mechanism can terminate the process
tree and inspection shows a plausible hang, unbounded wait, external call, or
child process, do not execute the measurement; report the missing deadline as
a blocker and propose a safer target or environment.

## Baseline Commands

The commands below are templates. Reuse the validated `pytest_python` and
`perf_dir`, and set `pytest_target` to an explicit safe path or node ID. Add a
marker expression separately when that is the intended selection. Do not guess
stale node IDs; rediscover them from current source or collection output.

```bash
pytest_target=tests/path
"${pytest_python}" -m pytest --version
"${pytest_python}" -m pytest --help
```

These confirm the current environment but still load pytest and installed
plugins. Run them only after static plugin and environment inspection.

### Collection Baseline

```bash
/usr/bin/time -p \
  "${pytest_python}" -m pytest \
  --collect-only \
  -q \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"
```

Record the timing tool and output stream used. This result includes both pytest
startup and collection.

### Serial Wall-Time Baseline

```bash
/usr/bin/time -p \
  "${pytest_python}" -m pytest \
  -q \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"
```

Keep coverage, JUnit, profiling, xdist, testmon, and other instrumentation out
of the primary wall-time baseline.

### Phase Duration Diagnostic

```bash
"${pytest_python}" -m pytest \
  -q \
  --durations=100 \
  --durations-min=0.05 \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"
```

`--durations` reports slow setup, call, and teardown entries. It is useful for
attribution, but compare primary wall time with an otherwise normal run.

### Plugin Inventory

Add `--collect-only` so plugin tracing does not accidentally run the suite:

```bash
"${pytest_python}" -m pytest \
  --trace-config \
  --collect-only \
  -q \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"
```

If persistent output is needed, redirect it only into the validated temporary
artifact directory.

### Import-Time Diagnostic

```bash
"${pytest_python}" -X importtime \
  -m pytest \
  --collect-only \
  -q \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}" \
  2> "${perf_dir}/import-times.log"
```

`-X importtime` reports self and cumulative import time. Its output can be
confusing for cached imports and may be unreliable in multi-threaded programs.
Use it to find candidates, not as a wall-time benchmark.

### Plugin-Autoload Comparison

Use this only as a diagnostic comparison:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "${pytest_python}" -m pytest \
  --collect-only \
  -q \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"
```

Collection may fail because addopts, markers, fixtures, or hooks depend on
autoloaded plugins. A faster failed collection is not an optimization. If a
project later disables autoload permanently, explicitly load every required
plugin once through a single supported mechanism and revalidate all lanes.

Modern pytest also supports `--disable-plugin-autoload`; the environment
variable remains useful when evaluating projects across multiple supported
pytest versions.

### Coverage Comparison

Measure the same selection without coverage and with the project's normal
coverage configuration. Do not combine the coverage difference with a fixture
or parallelism change.

```bash
pytest_package=your_package

/usr/bin/time -p \
  "${pytest_python}" -m pytest \
  -q \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"

/usr/bin/time -p \
  "${pytest_python}" -m pytest \
  -q \
  --cov="${pytest_package}" \
  --cov-report= \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  "${pytest_target}"
```

Honor repository policy when coverage is required in a local or pull-request
gate. The skill may recommend a separate coverage lane; it must not silently
remove a required gate.

## Targeted Fixture And Profiler Diagnostics

### Fixture Inventory

```bash
"${pytest_python}" -m pytest \
  tests/path/test_module.py::test_name \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  --fixtures-per-test
```

This identifies requested and autouse fixtures for the selected test.

### Fixture Execution Trace

```bash
"${pytest_python}" -m pytest \
  tests/path/test_module.py::test_name \
  --setup-show \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  -q
```

`--setup-show` executes the selected test and fixtures. Use it only after the
same safety preflight as an ordinary test run. `--setup-plan` can show the
planned fixture/test sequence without executing fixtures or tests, but
collection imports and hooks still run.

### End-To-End Pytest Profile

```bash
"${pytest_python}" -m cProfile \
  -o "${perf_dir}/slow-test.prof" \
  -m pytest \
  tests/path/test_module.py::test_name \
  -o "cache_dir=${perf_dir}/pytest-cache" \
  -q
```

This profiles the complete pytest invocation, including startup, imports,
plugins, fixtures, the test, and teardown. It is not pure function-level timing
and must not be compared directly with an unprofiled wall-time baseline.

Inspect cumulative time:

```bash
"${pytest_python}" - "${perf_dir}/slow-test.prof" <<'PY'
import pstats
import sys
from pstats import SortKey

stats = pstats.Stats(sys.argv[1])
stats.strip_dirs()
stats.sort_stats(SortKey.CUMULATIVE)
stats.print_stats(50)
PY
```

Use `SortKey.TIME` as a secondary view for time spent inside functions rather
than their callees.

## Repeated Sampling

Run important comparisons at least three times and usually five when variance
is meaningful. Record every raw sample; report the median and a spread such as
minimum/maximum or median absolute deviation.

Keep constant:

- source/diff identity
- interpreter, dependency, pytest, and plugin versions
- command, selection, environment variables, markers, and addopts
- worker count and distribution mode
- coverage and reporting configuration
- CPU/power mode and resource limits
- warm-cache or cold-cache policy
- external service and dataset state

Alternate baseline and candidate order when cache warming or thermal behavior
could bias the result. Do not discard outliers without a documented causal
reason.

## Like-For-Like Comparison

Before accepting a speedup, require equivalent:

- command purpose and test selection
- collected and deselected counts
- passed, failed, skipped, xfailed, xpassed, error, and rerun counts
- exit status
- plugin and configuration set
- instrumentation, coverage, and reporting mode
- source state other than the candidate optimization

Selection changes, marker lanes, testmon, and CI shards can shorten feedback
while intentionally running fewer tests. Report those as feedback-architecture
improvements and preserve an independent complete correctness gate.

## Interpreting Results

- Slow startup or collection: inspect plugin loading, conftest imports,
  module-level work, dynamic parametrization, discovery scope, and repeated
  per-worker collection.
- Slow setup: inspect fixture frequency, autouse reach, database/application
  creation, migrations, containers, subprocesses, and large temporary data.
- Slow call: inspect external boundaries, sleeps, polling, production-strength
  algorithms, large payloads, and slow production code.
- Slow teardown: inspect recursive deletion, rollback, finalizer ordering,
  process termination, and container or service shutdown.
- Slow only under coverage or reporting: treat instrumentation as a separate
  lane or reduce duplicate report generation without weakening policy.
- Faster only with fewer tests or different outcomes: reject as a like-for-like
  optimization.

## Official References

- [pytest execution duration](https://docs.pytest.org/en/stable/example/simple.html#profiling-test-execution-duration)
- [pytest command-line reference](https://docs.pytest.org/en/stable/reference/reference.html)
- [pytest plugin loading and autoload controls](https://docs.pytest.org/en/stable/how-to/plugins.html)
- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest temporary paths](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
- [Python `-X importtime`](https://docs.python.org/3/using/cmdline.html#cmdoption-X)
- [Python `cProfile` and `pstats`](https://docs.python.org/3/library/profile.html)
