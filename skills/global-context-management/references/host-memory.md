# Host Memory Headroom

Use the memory available on the current execution host, in binary GiB
(1 GiB = 1,073,741,824 bytes). Check before workloads, monitor throughout owned
work and after cancellation, and close the monitor at task end.

| Available headroom | Required response |
| --- | --- |
| More than 4 GiB | Continue normally. |
| At most 4 GiB, more than 2 GiB | Warn the user; continue the task and launches. |
| At most 2 GiB | Stop owned work safely, preserve progress and report the shortage. |
| Unknown/unreliable | Report unavailable measurement; pause heavy work. |

At the stop floor, say: "The host is running out of memory; I stopped my work
to protect the remaining headroom." Only lightweight communication, diagnosis
and safe cleanup continue until headroom exceeds 2 GiB. Resume from preserved
state; warn while headroom is at or below 4 GiB. Do not reset workflow evidence
or repair budgets. Native pressure is advisory; it does not impose a second
stop threshold above 2 GiB.

## Commands

Run these helpers from the reviewed source or installed skill directory using
Python 3.11 or later. No package installation or external service is required.
These are helper commands, not additional public skill invocation flags.

```bash
python3 -B scripts/host_memory.py snapshot
python3 -B scripts/host_memory.py watch --seconds 300
python3 -B scripts/host_memory.py run -- python3 -B path/to/test_suite.py
python3 -B scripts/host_memory.py check-policy --instructions /absolute/AGENTS.md
```

- `snapshot`: one bounded observation; warning exits 0, critical/unknown 75.
- `watch --seconds N`: read-only monitoring for 0 < N <= 43200 seconds;
  reports status transitions to stderr. Warning continues; critical/unknown
  exits 75. The caller must observe the result and stop owned external work.
  An unobserved background watcher does not enforce anything.
- `run -- COMMAND...`: check before launch and about once a second while the
  foreground command runs. Warning admits and continues work. Critical/unknown
  stops the exact owned process group and returns 75. Normal completion retains
  the command's exit code; launch failure returns 127, supervisor/cleanup failure
  70, cancellation 130. Child exit codes can overlap these codes; inspect the
  bounded status messages as well. Command output remains the caller's output.
- `check-policy --instructions PATH`: read-only focused policy check, including
  partially configured native homes. Requires a regular owned file without
  symlink components, multiple hard links or group/world write permission.
  Reports `STATIC_PASS`/0 or `NOT_ALIGNED`/1; never certifies runtime loading.
- `-h, --help`: helper usage; no observation or mutation.

Supervision starts a private session anchor and keeps its identity pinned until
cleanup. It sends TERM, allows a brief grace period, then KILL to its exact
group and reaps the anchor. Do not wrap operations that cannot tolerate bounded
interruption without first selecting their supported checkpoint/cancellation
path. No broad process-name matching, unrelated process termination, OS tuning,
sudo, credentials, raw environment dumps or memory stress tests are needed.

## Resource Owners And Limits

For a multi-step task, keep a bounded watcher active and consume its results
between steps; prefer `run` for supervised foreground builds/tests. On critical
or unknown status, dispatch cleanup through the already-authorized resource
owner and verify completion before claiming the workload stopped. A finite
watcher must be renewed while work continues. Track its exact process/session
and close it in the task's normal and interrupted cleanup paths.

Docker daemon allocations, detached/escaped process groups, browser MCP server
lifetimes, remote workloads and the agent application's own heap are outside
the foreground supervisor's containment. Use exact container/session/resource
identities and the existing owner's stop/close operation. Never kill user apps
or OS services such as directory services. If cleanup cannot be verified,
report it explicitly. Do not claim that killing a CLI stopped a daemon job.

Sampling cannot prevent a sudden allocation between observations or changes by
unrelated applications. The policy is an operational stop rule; it does not
reserve physical RAM. A task-bound helper is explicitly invoked and does not
install a daemon or hook. Global instructions direct agent behavior; their
presence alone does not prove that every future session follows them.

## Measurement

On macOS, use the page size reported by `vm_stat` and sum its printed free,
inactive and speculative pages, bounded by `hw.memsize`. This is an estimated
reclaimable amount, not Activity Monitor's pressure graph or raw free memory.
The Apple implementation subtracts speculative pages from printed free, so
adding printed speculative once is intentional. Purgeable pages are not added
again, nor are compressed pages or swap counted as spare RAM.

On Linux, use `MemAvailable`, not `MemFree`. The current conservative observer
reports unknown for visible container markers or non-root cgroup membership:
this includes many systemd user sessions. Such hosts need a verified native
visibility adapter before this helper can admit heavy work; do not represent
guest `/proc` counters as an outer-host reserve. Other operating systems are
currently unsupported and report unknown. Fixed native probes have timeouts
and bounded accepted output; failures expose only a fixed diagnostic reason.

## Policy Ownership And Validation

`config-codex/assets/AGENTS.md.template` and
`config-claude/assets/CLAUDE.md.template` contain the same compact policy as
the helper's `POLICY`. Setup owners patch just that section when requested,
preserving other instructions and partial-home configurations. Full setup
checkers retain their broader contracts. Fresh native session loading must
be observed independently from policy checks and source/installed parity.

Tests use synthetic thresholds and tiny subprocesses to cover warning
continuation, the stop floor, observation loss, cancellation, cleanup scope,
parser failures and policy drift. Never exhaust actual host memory to test
this policy. Native platform qualification is separate from parser tests.

## Sources

- [Apple vm_stat implementation](https://raw.githubusercontent.com/apple-oss-distributions/system_cmds/main/vm_stat/vm_stat.c)
  defines printed free versus speculative pages.
- [Apple Activity Monitor memory](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)
  explains pressure and memory categories.
- [Linux proc documentation](https://docs.kernel.org/filesystems/proc.html)
  defines `MemAvailable`.
- [Codex global instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
  and [Claude personal instructions](https://code.claude.com/docs/en/memory)
  define their native loading paths.
