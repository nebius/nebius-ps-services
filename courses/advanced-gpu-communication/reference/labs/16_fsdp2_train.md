# Lab 16: Observe FSDP2 sharded training state

Fully Sharded Data Parallel reduces persistent state per rank by distributing parameters, gradients, and optimizer state, but computation still needs appropriately materialized parameters. This lab applies FSDP2 to the tiny transformer on two H100 nodes. You will trace state ownership and communication rather than assume that sharding halves every allocation or guarantees faster training.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass distributed preflight and qualify the Training environment's FSDP2 API. Review DDP first. The example is intentionally small enough for mechanics; it is not a production-scale memory or network benchmark.

## Concepts and code path

The code constructs the model, applies `fully_shard` to each transformer block and then the root, and creates AdamW afterward. `MixedPrecisionPolicy` sets computation and reduction formats separately. Parameters materialize for computation and gradients reduce to their owners; gathered parameters, activations and communication buffers still contribute to peak memory. Reporting uses the slowest rank and maximum allocation.

## Practice

`labs/16_fsdp2_train.py` trains a tiny transformer with FSDP2 sharding and mixed precision. It checks finite loss and local-shard updates and writes elapsed time, model shape, and maximum rank memory usage.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/16_fsdp2_train/logs/%j.out" \
  --error="$PWD/results/16_fsdp2_train/logs/%j.err" \
  slurm/training_two_rank.sbatch \
  labs/16_fsdp2_train.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/16_fsdp2_train/logs/$LAB_JOB_ID.out"
cat "results/16_fsdp2_train/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Confirm finite mean loss and inspect `world_size`, `slowest_rank_elapsed_ms`, and `max_rank_peak_allocated_mib`. These checks demonstrate bounded execution, not a full reference comparison of all gradients or checkpoint/restart behavior.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Slowest rank elapsed (seconds) | `slowest_rank_elapsed_ms` | `s` |
| Final mean loss | `final_mean_loss` | `none` |
| Maximum rank peak allocated memory | `max_rank_peak_allocated_mib` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 16_fsdp2_train \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the supplied sharded path through the two-node launcher. When comparing against DDP, independently verify matching global workload, precision and timed operations instead of assuming matching profile names prove equivalence.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/16_fsdp2_train/logs/%j.out" \
  --error="$PWD/results/16_fsdp2_train/logs/%j.err" slurm/training_two_rank.sbatch labs/16_fsdp2_train.py --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/15_ddp_train/logs/%j.out" \
  --error="$PWD/results/15_ddp_train/logs/%j.err" slurm/training_two_rank.sbatch labs/15_ddp_train.py --profile small
```

For the guided candidate, run:

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/16_fsdp2_train/logs/%j.out" \
  --error="$PWD/results/16_fsdp2_train/logs/%j.err" slurm/training_two_rank.sbatch labs/16_fsdp2_train.py --profile small --zero-grad-fill
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw persistent shards separately from temporarily gathered parameters. Which memory survives between steps? Which communication is needed before forward and during backward? Explain why small models can become slower when communication overhead dominates.

Capture a separate diagnostic run:

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 --cpus-per-task=32 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/16_fsdp2_train/logs/capture-%J-%t.out" \
  --error="results/16_fsdp2_train/logs/capture-%J-%t.err" \
  bash slurm/capture_ranks.sh 1 \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt,nccl \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/16_fsdp2_train/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{RANK}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/16_fsdp2_train.py --profile small
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Compare the default gradient release with --zero-grad-fill at fixed model, data, seed and update count. Inspect the zero_grad range and complete-step time, require the same finite-loss/update checks, and compare final loss within numerical tolerance. Independently inspect whether clearing traffic or buffer allocation dominates before choosing a policy.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

An unavailable FSDP2 interface is an environment qualification blocker. A memory peak above your shard estimate may include materialization and activations. Do not silently switch to an older sharding API or claim the same experiment passed.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Choose sharding from a state-placement and critical-path model. Extend numerical verification and checkpoint handling separately before using the pattern for a larger model; two nodes establish mechanics, not production scaling.
