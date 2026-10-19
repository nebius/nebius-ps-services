# Helper Contracts

The subprocess helpers require POSIX process groups (Linux or macOS). Other
hosts fail before launch; use equivalent native observations with the same
evidence contract. The diagnostic method itself is platform independent.
Timeouts terminate the owned process group; detached sessions are outside that
group, so run only trusted commands that do not deliberately escape it.

## Selective Evidence Collection

`collect_evidence.py --root PATH` defaults to repository and platform identity.
Repeat `--section repository|platform|locale|resource-limits|filesystem` to choose
an explicit set; repeat `--tool NAME` for known version probes. No tool versions
are probed by default. `--section filesystem` tests only a fresh system temporary
directory, not the target mount. `--output PATH --pretty` writes atomic private
JSON; omitted output uses stdout. No source, environment values or Git remotes
are emitted. Dirty status is unknown if incomplete evidence cannot prove it.

Schema 2 distinguishes `ok`, `failed`, `timeout`, `unavailable`, `skipped`,
`truncated` and `changed-during-read`. Each subprocess stream is limited during
ingestion to 64 KiB; Git-status stdout has a 2 MiB limit. Version probes use at
most four workers and five seconds each, within a 30-second collection budget.
Manifest reads have 10 MiB per-file and 50 MiB aggregate budgets. Symlinks and
special files are skipped. A complete SHA-256 requires stable file identity,
size and modification metadata; incomplete reads have `sha256: null`.
Incomplete collection exits 2 while preserving its explicit observation status.

Examples:

```sh
python3 scripts/collect_evidence.py --root . --tool python3 --pretty
python3 scripts/collect_evidence.py --root . --section platform --section resource-limits
```

## Repeated Executions

`repeat_command.py --runs N --timeout SECONDS -- COMMAND ARGS...` measures an
authorized, safe, idempotent reproducer. It inherits the command's effects.
Do not pass secrets in argv. Use `--out PATH` for atomic private output and
`--max-tail-bytes N` to bound each retained stream tail (maximum 65536).

Schema 2 has `attempted_runs`, `launched_runs`, `completed_runs`,
`interrupted_runs`, and `launch_error_count`. Every attempted launch has an
outcome: `launch_error`, `interrupted`, `timeout`, `nonzero`, or `pass`.
Launch errors contribute neither application rates nor execution timings.
Completed executions include timeouts; failures are nonzero exits plus timeouts.
When none completes, rates and aggregate execution timings are null. Interrupted
executions have separate timings. Signature clusters include their outcome.
The helper's successful exit means measurement completed, not workload success;
launch errors exit 2 and interruption exits 130.

Consecutive executions may share caches, load, data and state. Do not infer that
repeated passes prove a defect absent. Confidence bounds need justified sampling
assumptions; report frequency and conditions without a probability claim otherwise.

`compare_evidence.py` remains generic JSON comparison. Explicitly exclude volatile
fields using its JSON-pointer interface; never silently ignore a causal field.

Sources: [Python subprocess](https://docs.python.org/3/library/subprocess.html),
[NIST proportion intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).
