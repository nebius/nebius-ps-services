# Soperator Slurm Upgrade Smoke Jobs

This folder contains public, generic Slurm jobs for testing Soperator upgrade
behavior while jobs are running. They are intentionally hardware-neutral: the
CPU job does not branch on AMD or Intel CPU models, and the GPU job only asks
Slurm for GPUs and prints the visible NVIDIA devices when `nvidia-smi` exists.

These samples test scheduler allocation, job visibility, cancellation, requeue,
wait, and interactive upgrade-policy behavior. They are not GPU benchmarks,
NCCL tests, storage tests, or application performance tests.

## Submit CPU Jobs

From this directory:

```bash
./submit-soperator-smoke.sh --kind cpu --partition cpu --count 10
```

By default, each job runs for 30 minutes and requests 35 minutes of Slurm wall
time. Change the duration with:

```bash
./submit-soperator-smoke.sh --kind cpu --partition cpu --count 10 --run-minutes 60 --wall-minutes 65
```

## Submit GPU Jobs

GPU jobs are submitted through the same script. GPU mode defaults to the `gpu`
partition and one GPU per job:

```bash
./submit-soperator-smoke.sh --kind gpu --partition gpu --count 10 --gpus-per-job 1
```

Use `--gpus-per-job` to request more GPUs per job when the partition supports
that shape. The job stays neutral across L40S, H100, H200, B200, B300, and
other NVIDIA GPU generations because it does not depend on model-specific
features.

## Repeated Jobs And Array Mode

The default submit mode is `loop`, which sends one `sbatch` command per job and
uses unique names such as `sop-cpu-smoke-01` or `sop-gpu-smoke-01`:

```bash
./submit-soperator-smoke.sh --kind cpu --partition cpu --count 10
```

For compact bulk submission, use Slurm array mode:

```bash
./submit-soperator-smoke.sh --kind gpu --partition gpu --count 10 --submit-mode array
```

Use `--dry-run` to inspect the generated `sbatch` commands without submitting
anything:

```bash
./submit-soperator-smoke.sh --kind gpu --count 3 --dry-run
```

## Node Sharing And Exclusive Placement

The default behavior allows Slurm to place multiple smoke jobs on the same node
when the partition policy permits it. That is useful for upgrade-policy demos
because you can cancel one job, wait for another, or select different policies
for multiple running jobs on the same node.

Use `--exclusive` when you want each job allocation to avoid sharing a node,
subject to the cluster's Slurm policy:

```bash
./submit-soperator-smoke.sh --kind cpu --partition cpu --count 10 --exclusive
```

## QOS, Account, Requeue, And Output

Pass Slurm accounting options when your cluster requires them:

```bash
./submit-soperator-smoke.sh --kind gpu --qos normal --account my-account --requeue
```

Slurm output files are written to `slurm-smoke-logs/` by default. Change that
with `--output-dir`:

```bash
./submit-soperator-smoke.sh --kind cpu --output-dir /shared/slurm-smoke-logs
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
