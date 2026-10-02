# Lab 03: Verify the distributed training allocation

Training correctness depends on each process owning the intended device and participating in the same communication group. This lab verifies the two-node, two eight-H100 workers, using one participating rank per worker before DDP, FSDP2, or parallelism experiments. You will establish placement and communication readiness separately from whether a model fits, converges, or scales efficiently on that platform.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Activate the Training environment and follow the [cluster runbook](../cluster-smoke-test.md). This particular lab requires two ranks on distinct nodes; the single-GPU training labs perform their own device checks.

## Concepts and code path

The shared `common.py` helpers validate H100 visibility, map ranks to devices, initialize NCCL, and gather node identities. The lab checks the declared two-node topology and reduces known values. Model construction and loss computation are deliberately absent so that a platform failure can be diagnosed independently of training logic.

## Practice

`labs/03_training_readiness.py` checks the training runtime using two GPU ranks on distinct hosts and an NCCL all-reduce. It writes the observed world size, host count, and collective correctness.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/03_training_readiness/logs/%j.out" \
  --error="$PWD/results/03_training_readiness/logs/%j.err" \
  slurm/training_two_rank.sbatch \
  labs/03_training_readiness.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/03_training_readiness/logs/$LAB_JOB_ID.out"
cat "results/03_training_readiness/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `two_nodes` and `nccl_all_reduce`, world size two, and two distinct hosts in the private placement evidence. Successful job submission without a completed correctness result is not acceptance.

Publication compares the recorded `world_size` and `distinct_host_count` in both runs. Keep both at two; a placement change invalidates the readiness comparison.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| World size | `world_size` | `none` |
| Distinct host count | `distinct_host_count` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 03_training_readiness \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Use the course's two-node launcher from this directory. Keep scheduler logs private and do not wrap the invocation in another launcher that would multiply the process count.

```bash
"$COURSE_PYTHON" labs/03_training_readiness.py --help
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/03_training_readiness/logs/%j.out" \
  --error="$PWD/results/03_training_readiness/logs/%j.err" slurm/training_two_rank.sbatch labs/03_training_readiness.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw the rank/device mapping and explain why each node uses local rank zero. Which later failures could still occur after this check passes, such as model memory exhaustion or mismatched collective order?

Capture a separate diagnostic run:

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 --cpus-per-task=32 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/03_training_readiness/logs/capture-%J-%t.out" \
  --error="results/03_training_readiness/logs/capture-%J-%t.err" \
  bash slurm/capture_ranks.sh 1 \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt,nccl \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/03_training_readiness/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{RANK}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/03_training_readiness.py --profile small
```

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Open both rank reports. Inspect lab_workload, CUDA streams and NCCL activity for the readiness all-reduce; require both ranks to participate before interpreting later training traces. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Resolve incorrect allocation or missing devices through the cluster owner. Preserve communication errors and environment identity. Do not alter network configuration, sharing modes, or drivers as part of this learner exercise.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Platform readiness is necessary but does not validate a training algorithm. Continue to DDP and FSDP2 using the already-understood single-GPU training step; revisit the tiny-model and loss-mask labs if needed.
