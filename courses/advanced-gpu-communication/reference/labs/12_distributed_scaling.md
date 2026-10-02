# Lab 12: Compare one GPU with two-node DDP

Adding a GPU adds both compute capacity and communication work. This lab runs a bounded training step on one GPU and through two-node DistributedDataParallel while preserving global batch size. You will calculate global throughput and scaling efficiency from the slowest participating rank rather than assuming that twice the devices should halve step time.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass the two-node preflight and use matching environments. The global batch must be divisible by world size. This topology has an inter-node link, not a shared NVLink/NVSwitch fabric.

## Concepts and code path

The model applies a linear projection, GELU and another linear projection. Its loss is the mean squared output. Each step clears gradients, computes the loss, calls backward and updates parameters with AdamW. AdamW maintains running averages of gradients and squared gradients and applies weight decay separately from the gradient update. The one-rank path runs locally. The script splits fixed global samples across ranks; DDP synchronizes gradients during backward. Reporting uses the slowest rank. A smaller local batch can also change kernel efficiency; finite loss alone does not establish equal trajectories or convergence.

## Practice

`labs/12_distributed_scaling.py` trains the same synthetic multilayer perceptron on one GPU or distributed ranks while keeping global batch fixed. It checks finite losses and writes slowest-rank step latency and samples per second.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/12_distributed_scaling/logs/%j.out" \
  --error="$PWD/results/12_distributed_scaling/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/12_distributed_scaling.py --profile small --global-batch 64
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/12_distributed_scaling/logs/$LAB_JOB_ID.out"
cat "results/12_distributed_scaling/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require finite loss on every rank. Compare `global_batch`, `median_step_ms`, `samples_per_second`, and the slowest-rank timing scope. This gate does not compare every gradient or update against a one-process reference; do not claim full training equivalence from finite loss alone.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Median step (seconds) | `median_step_ms` | `s` |
| Samples per second | `samples_per_second` | `samples/s` |
| Global batch | `global_batch` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 12_distributed_scaling \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Keep the declared global batch identical across these jobs. They form the scaling pair; running only the second command cannot establish a speedup.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/12_distributed_scaling/logs/%j.out" \
  --error="$PWD/results/12_distributed_scaling/logs/%j.err" slurm/single_gpu.sbatch labs/12_distributed_scaling.py --profile small --global-batch 64
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/12_distributed_scaling/logs/%j.out" \
  --error="$PWD/results/12_distributed_scaling/logs/%j.err" slurm/two_node.sbatch labs/12_distributed_scaling.py --profile small --global-batch 64
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Compute speedup as one-rank time divided by two-rank time, then divide by two for efficiency. Explain how communication and reduced local batch size can each limit the result.

Capture a separate diagnostic run:

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 --cpus-per-task=32 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/12_distributed_scaling/logs/capture-%J-%t.out" \
  --error="results/12_distributed_scaling/logs/capture-%J-%t.err" \
  bash slurm/capture_ranks.sh 1 \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt,nccl \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/12_distributed_scaling/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{RANK}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/12_distributed_scaling.py --profile small --global-batch 64
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Use one versus two ranks at the same global batch as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

An indivisible global batch is rejected intentionally. If one rank stalls, global progress stalls too; inspect private placement and communication logs. Do not average away the straggler to improve the throughput report.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Scaling is an end-to-end property with an explicit workload contract. Add matched-state gradient/update equivalence before using a modified training path, and use Lab 13 to investigate communication overlap separately.
