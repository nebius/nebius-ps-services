# Soperator Slurm Upgrade Job Tests

This folder contains public, generic Slurm job tests for exercising Soperator upgrade
behavior while jobs are running. They are intentionally hardware-neutral: the
CPU job does not branch on AMD or Intel CPU models, and the GPU job only asks
Slurm for GPUs and prints the visible NVIDIA devices when `nvidia-smi` exists.

These samples test scheduler allocation, job visibility, cancellation, requeue,
wait, and interactive upgrade-policy behavior. They are not GPU benchmarks,
NCCL tests, storage tests, or application performance tests.

## Run On The Login Node

From the local checkout root, `--login` stages this directory on the login
node and opens an interactive SSH shell in the staged directory. The default
staging path is a unique
`/root/testjobs-<UTC timestamp>-<process ID>` directory created with mode `0700`:

```bash
./examples/slurm-jobs/submit-job-test.sh --login <login-external-ip>
```

From the opened login-node shell, run the submit or watch command you need:

```bash
./submit-job-test.sh
./submit-job-test.sh --count 2 --heartbeat-seconds 2
./submit-job-test.sh --watch-jobs
```

Use an explicit, not-yet-existing staging path when you need a predictable
location:

```bash
./examples/slurm-jobs/submit-job-test.sh \
  --login <login-external-ip> \
  --login-remote-dir /root/my-private-testjobs
```

`--login-remote-dir` requires `--login`. The path must be a child of `/root`,
use only letters, digits, `.`, `_`, `/`, and `-`, and must not contain `.` or
`..` path segments. Remote staging fails if the path already exists, preventing
the helper from changing permissions or overwriting files in an existing
directory. The helper does not delete staged files, so retain them for upgrade
evidence or remove them explicitly after the campaign.

The login target is intentionally limited to a DNS name or IPv4 address and
the remote account is fixed to `root`; arbitrary SSH options are not accepted.
`--login-remote-dir` fails unless `--login` is present. Submission and watch
options cannot be combined with `--login`; run them after the interactive
shell opens. With `--login`, `--dry-run` prints the private-directory SSH,
SCP, and interactive `ssh -t` commands without connecting to the login node.

## Submit GPU Jobs

GPU jobs are the default because the normal Nebius Slurm partition is
`main*`, which is a GPU partition. The smallest default submission is:

```bash
./submit-job-test.sh
```

By default, the wrapper submits one GPU job, requests one GPU, and lets Slurm
use its default partition. Change the count with:

```bash
./submit-job-test.sh --count 10
```

The GPU template is also selected automatically for `--partition main`:

```bash
./submit-job-test.sh --partition main --count 10
```

Use `--gpus-per-job` to request more GPUs per job when the partition supports
that shape:

```bash
./submit-job-test.sh --count 10 --gpus-per-job 2
```

The job stays neutral across L40S, H100, H200, B200, B300, and other NVIDIA GPU
generations because it does not depend on model-specific features.

## Submit CPU Jobs

For a CPU partition, choose the CPU job template explicitly:

```bash
./submit-job-test.sh --part-type cpu --partition cpu --count 10
```

Each job runs for 30 minutes with 35 minutes of requested Slurm wall time by
default. Change the count and duration with:

```bash
./submit-job-test.sh --part-type cpu --partition cpu --count 10 --run-minutes 60 --wall-minutes 65
```

## Heartbeat Evidence

Both templates write timestamped heartbeats every 30 seconds by default. For a
disposable upgrade campaign, use two-second heartbeats so an interruption is
visible with a narrow evidence gap:

```bash
./submit-job-test.sh --run-minutes 60 --wall-minutes 65 --heartbeat-seconds 2
```

`--heartbeat-seconds` must be a positive integer. The helper exports it to the
batch job as `HEARTBEAT_SECONDS`; it changes evidence frequency, not the Slurm
job's requested wall time.

## Repeated Jobs And Array Mode

The default submit mode is `loop`, which sends one `sbatch` command per job and
uses unique names such as `sop-cpu-job-test-01` or `sop-gpu-job-test-01`:

```bash
./submit-job-test.sh --count 10
```

For compact bulk submission, use Slurm array mode:

```bash
./submit-job-test.sh --partition main --count 10 --submit-mode array
```

Use `--dry-run` to inspect the generated `sbatch` commands without submitting
anything:

```bash
./submit-job-test.sh --count 3 --dry-run
```

## Node Sharing And Exclusive Placement

The default behavior allows Slurm to place multiple job-test workloads on the
same node when the partition policy permits it. That is useful for
upgrade-policy demos because you can cancel one job, wait for another, or
select different policies for multiple running jobs on the same node.

Use `--exclusive` when you want each job allocation to avoid sharing a node,
subject to the cluster's Slurm policy:

```bash
./submit-job-test.sh --part-type cpu --partition cpu --count 10 --exclusive
```

## QOS, Account, Requeue, And Output

Pass Slurm accounting options when your cluster requires them:

```bash
./submit-job-test.sh --qos normal --account my-account
```

The helper explicitly passes `sbatch --no-requeue` by default so preservation
jobs do not inherit a cluster-level `JobRequeue=1` default. Use `--requeue` only
for disposable action probes that are intended to test requeue behavior; that
option replaces `--no-requeue` with `sbatch --requeue`:

```bash
./submit-job-test.sh --qos normal --account my-account --requeue
```

Slurm output files are written to `slurm-smoke-logs/` by default. Change that
with `--output-dir`:

```bash
./submit-job-test.sh --output-dir /shared/slurm-smoke-logs
```

## Upgrade Policy Demo

After submitting jobs, run the Soperator upgrade and keep `squeue` open in a
separate login-node shell when the operator needs to inspect active jobs:

```bash
nebius-cxcli soperator upgrade CONFIG_YAML --target TARGET --execute --approve
squeue --iterate=5
```

The smoke jobs exit non-zero on `TERM` or `INT`, so interruption, cancellation,
and requeue behavior are visible in Slurm output. Keep at least one dedicated
preservation job out of cancel/requeue tests; use separate probe jobs when
exercising the TUI action journal intentionally.

## Watch During Upgrade

Use `--watch-jobs` from the login node while the upgrade is running to produce
a timestamped proof stream from Slurm's live queue:

```bash
./submit-job-test.sh --watch-jobs
```

The watcher matches `sop-*-job-test*` job names by default and polls `squeue`.
On color-capable terminals, only the state value is colorized: `RUNNING` is
green, queued states are yellow, completed states are cyan, configuration
states are blue, suspended states are magenta, and failed states are red.
`NO_COLOR` and `TERM=dumb` keep the output plain.
An unallocated pending job is tracked by exact JobID, submit time, and
`Restarts` without requiring a start time or node allocation. As soon as the job
starts, the watcher captures its start time and allocation through `scontrol`;
every later sample must keep that running lineage unchanged. Brief
controller-RPC visibility gaps are reported and retried without discarding the
last baseline. Scope the proof to known IDs when needed:

```bash
./submit-job-test.sh --watch-jobs --watch-job-ids 12345,12346
```

Use `--watch-duration <seconds>` only when you want an explicit maximum watch
window, and use `--watch-once` for a single snapshot. If `sacct` is available,
the watcher requires the final allocation record to match the pre-upgrade
baseline with `Restarts=0`, `State=COMPLETED`, and `ExitCode=0:0`. A job that
finishes before the watcher captures its baseline is not accepted as continuity
evidence.
