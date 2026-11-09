# Source suites and test evidence

Reuse the project's tests and harness. Adapt the execution and evidence pattern;
do not copy another product's tests, test count, privileged recipe or release gate.
Suite names/categories describe project intent, not a fixed test framework.

## Freeze and collect before execution

1. Select exact project, suite entrypoints, source or installed target, runtime
   class, assertions, budgets and supported capability requirements.
2. Freeze source/test/harness/dependency inputs, including relevant dirty bytes,
   file **and directory** modes and contained symlink targets. The optional
   `source_manifest.py ROOT PATHS_JSON` hashes an explicit reviewed list and
   ancestor directories without recursively discovering unreviewed files. It
   includes intermediate symlinks/traversed directories and rejects symlink-parent
   traversal, absolute links and escaping links. Verify the staged snapshot again;
   the helper neither copies files nor proves runtime permissions.
3. Prepare the test environment. A restricted service/test user must actually
   traverse and read the staged bundle. Repair the staging owner if inaccessible;
   do not weaken the service sandbox to make a test run. Prepare source test
   dependencies separately from predecessor images and installed software.
4. Collect the expected test IDs in a separate process against the frozen inputs
   and the declared environment/selection. Collection may execute imports. It is
   allowed only during an authorized full run in disposable preparation, never
   `--plan-only`, `--list-suites` or `--preflight-only`.
5. Verify collection process completion, freeze its inventory, then expand/seal
   the plan. Run suites in fresh writable state, preserving that inventory.
   Unexpected collection drift is incomplete coverage, not a new expectation.

For multiple environments/trials, verify the inventory applies identically in
each; split campaigns when selections differ. Capture original failing attempts.
Changing source, dependencies, harness or fixture state creates new identities.
Timing distinguishes preparation, collection, execution, verification and cleanup.

## Neutral inventory and receipt

The inventory schema is `test-software-in-container.inventory.v2`. It is sealed
with `tsc_support.contracts.seal` and contains:

- `suite_sha256`, `input_sha256` (full frozen bundle), `selection_sha256`.
- `runner`: exactly `name` and `version`.
- `selected`: unique ordered test IDs after selection, not a test-file count.
- `deselected`: objects with exact `id` and a nonempty `reason`.
- `complete: true`: successful independent collection; the harness also verifies
  its actual process exit before accepting this assertion.

The suite definition embeds this `inventory` alongside its definition `sha256`,
category `kind` and optional predeclared `exemptions`. No inventory produces a
blocked plan, not invented zero-test success. Inventory and result JSON are
limited to 4 MiB; identifiers/reasons are bounded single-line strings (test IDs up
to 256 characters). Split or adapt oversized suites explicitly before execution.

Each `suite.<name>` check observation contains a `receipt` with schema
`test-software-in-container.suite-result.v2`:

| Field | Meaning |
| --- | --- |
| `case_id`, `plan_sha256` | Exact planned trial and campaign, preventing accidental cross-case receipt reuse. |
| `inventory_sha256` | Digest of the previously frozen collection. |
| `suite_sha256`, `input_sha256`, `selection_sha256`, `runner` | Exact inventory identities. |
| `collected`, `deselected` | Execution-process selection; compare to inventory, including order and reasons. |
| `completed`, `exit_code` | Runner session termination observation; integer code or null before termination. |
| `process_completed`, `process_exit_code` | **Harness-observed** process termination and return code (integer -255 through 255, or null). Preserve a Python harness's negative POSIX signal return code; mark known interruption with `interrupted: true`. A plugin cannot observe its own process exit. Preserve the raw plugin terminal and attach these observations in a new result document. |
| `interrupted` | Whether execution was interrupted; interruption never passes. |
| `errors` | Collection/runner errors as objects with `phase` and nonempty `reason`. |
| `collection_skips` | Module-level skips with `id` and `reason`; incomplete coverage, distinct from import errors. |
| `observed` | Per-test objects with `id`, `origin` and `phases`. |
| `phase_seconds` | Finite nonnegative setup/call/teardown timing totals. |

Each test origin has `kind: source|installed` and a bounded `reference` to the
declared origin observation. The agent independently inspects relevant import,
executable or service paths; a plugin's supplied label cannot attest every import
or subprocess. Source checks separately require `probe_origin: frozen-source`,
matching `source_sha256` and `restricted_access_verified: true`. Installed checks
require independent installed-artifact probes with source imports disabled.

Each observed phase (`setup`, `call`, `teardown`) has `outcome`, `seconds` and a
nonempty `reason` for nonpassing outcomes. Outcomes are `passed`, `failed`,
`skipped`, `xfail`, `xpass`. A setup skip/failure may explain an absent call;
otherwise missing phases remain incomplete. Duplicate/unexpected test IDs fail.

Any assertion, setup/teardown, collection or runner error fails. Unexpected passes
fail. Missing terminal/process evidence, interrupted, empty, all-skipped or missing
test coverage is incomplete. Pytest's no-tests exit 5 is incomplete; other nonzero
noninterrupted process exits fail. At least **one ordinary passing test per selected
suite/case** is required. Aggregate success cannot be borrowed from another suite.

An exemption names exactly one `id`, `phase` (setup/call), `outcome` (skipped/xfail)
and `reason`. Xfail is permitted only for call and also needs the exact predeclared
`failure_sha256`; it cannot excuse a different assertion. For pytest this hashes
`str(report.longrepr)` as UTF-8. Exemptions never excuse setup/teardown errors and
must be frozen before execution. All-exempt suites remain incomplete. No retry or
discarded earlier failure can turn the same trial into a pass.

`tsc_support.suite_contracts.assess_receipt` derives outcome/counts/gaps. `check_evidence.py`
rejects a check status that contradicts that result. Seals and structured receipts
detect drift and inconsistency; they are **not execution attestation**. Keep raw
private runner logs/evidence and independently review them. The non-pytest
[neutral receipt](../assets/neutral-suite-result.json) illustrates this format.

## Optional pytest reporter

Load `scripts/tsc_support/pytest_receipt.py` in an existing project pytest environment. The
adapter has been exercised with pytest 8.4.2; qualify other project versions before
relying on them. It observes public hooks without selecting tests, changing
outcomes, retrying, installing packages or operating containers. Known xdist,
rerunfailures, repeat, flaky and parallel plugins are rejected, even if loaded but
inactive. Disable them for this adapter or use a separately qualified reporter
that preserves every attempt/worker. Do not infer arbitrary custom retry support.

`TSC_PYTEST_CONFIG` names private JSON prepared by the harness. Common fields are
`mode: collect|run`, `suite_sha256`, `input_sha256`, `selection_sha256`,
`target: source|installed`, `origin_reference` and `output` (a new directory).
Run mode also requires `inventory` (frozen inventory path), `case_id` and
`plan_sha256`. Use absolute resolved paths here. The selection hash covers the
reviewed runner arguments/config/environment; the plugin checks supplied identities
but the harness independently freezes and verifies the actual launch.

Example commands **inside an authorized prepared test container**, with project
and reporter locations chosen by its harness:

```bash
TSC_PYTEST_CONFIG=/evidence/collect-config.json \
  python -m pytest -p tsc_support.pytest_receipt -p no:cacheprovider --collect-only tests
# Inspect actual collection process exit; freeze inventory.json, then seal the plan.
TSC_PYTEST_CONFIG=/evidence/run-config.json \
  python -m pytest -p tsc_support.pytest_receipt -p no:cacheprovider tests
# The harness records the actual process exit in a new case result.
```

Stage the complete `tsc_support` package and expose its parent directory for plugin
import, for example the staged `scripts/` directory. Adding `scripts/tsc_support/`
itself to PYTHONPATH cannot resolve `-p tsc_support.pytest_receipt`. Follow the
project's declared source policy. Installed tests must not gain the product source
through PYTHONPATH or a test dependency installation. Selectors must be the same
in both processes; only the collection flag and evidence destination differ.

The reporter creates a new 0700 directory, 0600 `initial.json`, numbered event
files and `terminal.json`; successful collection also writes `inventory.json`.
It writes terminal state after other finish hooks and preserves finish errors.
A crash may leave events without a terminal: retain them and report incomplete.
The terminal deliberately leaves process completion unset; only the external
harness can supply it. Module skips cannot silently disappear from collection.

Keep test IDs/reasons free of secrets. Runner logs and failed-test diagnostics
belong in private evidence; never copy confidential parametrized values into
public reports. Bound event count/output storage and timeout through the project
harness; the reporter is not a resource supervisor.

## Primary references

- [pytest collection and selection](https://docs.pytest.org/en/stable/how-to/usage.html)
- [pytest public hooks](https://docs.pytest.org/en/stable/reference/reference.html)
- [pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html)
- [Python subprocess return codes](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.returncode)
- [Docker platform and emulation boundaries](https://docs.docker.com/build/building/multi-platform/)
- [systemd container interface](https://systemd.io/CONTAINER_INTERFACE)

Verify version-sensitive runner/runtime behavior against these project sources.
