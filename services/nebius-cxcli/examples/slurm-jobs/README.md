# Soperator Slurm Upgrade Job Tests

This folder contains public, generic Slurm job tests for exercising Soperator upgrade
behavior while jobs are running. They are intentionally hardware-neutral: the
CPU job does not branch on AMD or Intel CPU models, and the GPU job only asks
Slurm for GPUs and prints the visible NVIDIA devices when `nvidia-smi` exists.

These samples test scheduler allocation, job visibility, cancellation, requeue,
wait, and interactive upgrade-policy behavior. They are not GPU benchmarks,
NCCL tests, storage tests, or application performance tests.

## Copy To The Login Node

From the local checkout root, let the submitter create `/root/testjobs`, copy
the example files there, and open an SSH session already landed in that
directory:

```bash
./examples/slurm-jobs/submit-job-test.sh --login <login-external-ip>
```

Run the submit commands below from that login-node SSH session so `sbatch` can
reach the cluster's Slurm controller.

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
./submit-job-test.sh --qos normal --account my-account --requeue
```

Slurm output files are written to `slurm-smoke-logs/` by default. Change that
with `--output-dir`:

```bash
./submit-job-test.sh --output-dir /shared/slurm-smoke-logs
```

## Upgrade Policy Demo

After submitting jobs, run the Soperator upgrade workflow with interactive job
policy selection so the operator can choose what to do with each affected
running job:

```bash
nebius-cxcli soperator upgrade CONFIG_YAML --target TARGET \
  --to-chart-version TARGET_VERSION \
  --job-policy interactive
```

Other job policies can still be selected for non-interactive runs when that is
the desired test. The smoke jobs exit non-zero on `TERM` or `INT`, so
interruption, cancellation, and requeue behavior are visible in Slurm output.

## Watch During Upgrade

Use `--watch-jobs` from the login node while the upgrade is running to produce
a timestamped proof stream from Slurm's live queue:

```bash
./submit-job-test.sh --watch-jobs
```

The watcher matches `sop-*-job-test*` job names by default, polls `squeue`, and
prints each observed job ID, state, elapsed time, remaining time, partition,
nodes, and name until the observed jobs finish and leave the queue. Scope the
proof to known IDs when needed:

```bash
./submit-job-test.sh --watch-jobs --watch-job-ids 12345,12346
```

Use `--watch-duration <seconds>` only when you want an explicit maximum watch
window, and use `--watch-once` for a single snapshot. If `sacct` is available,
the watcher also prints final accounting evidence for observed jobs before
reporting pass or fail.
