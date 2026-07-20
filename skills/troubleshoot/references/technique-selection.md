# Technique Selection

Choose a technique because it distinguishes hypotheses, not because it is
familiar. Verify exact commands and limitations in current official tool or
runtime documentation.

<!-- markdownlint-disable MD013 -->

| Causal question | Preferred technique | Required precondition | Common trap |
| --- | --- | --- | --- |
| Which change introduced the behavior? | Automated revision or artifact bisection | Equivalent good/bad environments and stable oracle | Bisecting an ambiguous signature |
| How often and in how many forms does it fail? | Repeated trials and signature clustering | Bounded deterministic runner | Treating retries as a fix |
| Which boundary first corrupts or rejects data? | Before/after boundary capture | Correlatable input | Adding broad logs everywhere |
| Is ordering or shared state required? | Dumps, event ordering, race detector, narrow schedule perturbation | Representative concurrent path | Assuming a clean detector run proves absence |
| Is memory lifetime or undefined behavior involved? | Memory/undefined-behavior sanitizer and input reduction | Matching architecture and build mode | Testing only an unrepresentative debug build |
| Why did latency or resource use regress? | Controlled benchmark and differential profiles | Stable workload and warmup | Calling the hottest frame the cause |
| Which input property is necessary? | Failure-preserving minimization, fuzzing, property testing | Stable signature | Minimizing toward another failure |
| Which environment difference matters? | Known-good/known-bad evidence comparison | Comparable snapshots | Dumping secret environment values |
| Where does a distributed request diverge? | Correlated traces, structured logs, metrics, contract checks | One request/job identity | Comparing unrelated aggregate windows |
| Is a cache or incremental artifact stale? | Preserve state, compare keys/dependencies, clean-state differential | Captured failing artifact identity | Clearing the cache before evidence capture |

<!-- markdownlint-enable MD013 -->

## Selection Rules

1. State the question and the competing hypotheses.
2. Predict the possible observations and how each updates the ledger.
3. Choose the narrowest technique that produces those observations safely.
4. Match optimization, architecture, runtime, load, permissions, and state to
   the failing conditions when they affect the mechanism.
5. Record tool coverage limitations. Dynamic diagnostics observe only executed
   paths and captured intervals.
6. Prefer deterministic helpers for repeated mechanics and retain causal
   judgment in the investigation.

## Bundled Helpers

### `collect_evidence.py`

Use for bounded local repository and environment identity. It records safe
platform, filesystem, resource-limit, tool-version, manifest-hash, and Git
summary facts. It reads recognized root manifest bytes only to hash them, never
emits their contents, and does not read Git remotes or environment values. Git
detection or status failures are reported as unknown rather than clean. Status
records are streamed into bounded counters; inspect the truncation field before
treating the individual counts as complete.

### `repeat_command.py`

Use when repetition is the experiment. It executes an argv vector without a
shell, bounds and redacts captured tails, terminates timed-out process groups on
POSIX, and terminates the timed-out parent process on other supported platforms.
It reports rates, timing, and signature clusters. It inherits the supplied
command's side effects; use only an authorized, safe, idempotent reproducer and
do not pass secrets in argv. A helper exit of `0` means the measurement ran, not
that every measured command passed.

### `compare_evidence.py`

Use to compare JSON evidence snapshots. Ignore volatile fields explicitly; do
not silently normalize a meaningful difference. Paths use JSON Pointer, with an
empty string for the document root. Difference output is bounded; inspect the
total, reported count, and truncation flag before drawing completeness claims.
