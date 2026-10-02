# Lab 21: Tune real DDP buckets and communication hooks

Gradient readiness only creates an opportunity for overlap; DDP must group gradients into buckets and communicate them in a valid order. This lab exposes that behavior and compares FP32 reduction with FP16, BF16 and PowerSGD communication hooks. You will observe actual bucket sizes, measure complete optimizer steps, and examine numerical drift against an independently evolving full-batch FP32 reference rather than assuming fewer bytes guarantee a better training run.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

DDP groups gradients into buckets and starts a collective as soon as each bucket is ready. Smaller buckets can communicate earlier but add more collective launches. One rank participates on each eight-H100 worker. The PyTorch 2.14 manifest remains the target; clean target qualification is separate. BF16 communication requires a supported NCCL newer than 2.9.6 and is an experimental framework API. No datasets or model downloads are needed. This two-rank mechanics experiment does not exercise all local peer links.

## Concepts and code path

The fixture has four FP32 Linear/Tanh stages, 256-wide for small and 1024-wide for h100. Tanh is the smooth hyperbolic tangent, bounded between -1 and 1. Each rank trains on an equal disjoint half of the same generated global batch. MSE uses a mean, so averaged rank gradients match the full-batch objective. Candidate and reference use plain SGD: `parameter -= learning_rate * gradient`. The independent reference evolves on the complete batch.

DDP receives `--bucket-cap-mb` and exactly one registered hook. The hook wrapper logs public GradBucket indices and uncompressed bytes before delegating to the framework's averaging, FP16, BF16 or PowerSGD implementation. Those bytes describe original gradients, not network traffic. Buckets can rebuild during startup. PowerSGD uses error feedback and warm start; `--power-start` must be at least two, and warm-up must include a compressed step. The default start of two is a short mechanics setting, not a recommendation for real training.

Reference checks follow timing. Both candidate and reference updates are checked against their actual gradients, including a nonzero-change requirement. The allreduce configuration also requires FP32 gradient/parameter agreement with the oracle. Lossy configurations report gradient and parameter trajectory relative L2 errors; those errors include accumulated state differences after earlier approximate steps. They are not isolated single-step compression error or convergence acceptance.

Consider 64 MiB of FP32 gradients and an illustrative effective link rate of 8 GiB/s: payload/rate alone is 7.8125 ms, before collective topology, startup and contention. Casting the payload to FP16 halves its nominal bytes, but conversion and reduction still cost time. If communication already fits beneath independent backward work, reducing its duration may not reduce step time including communication completion. A trace that shows less collective time but unchanged or worse step time is a valid reason to reject compression.

After Lab 19's synthetic readiness schedule, run Lab 21: Tune real DDP buckets and communication hooks. Compare two bucket caps with the allreduce hook first, using fresh jobs. Hold the selected cap fixed while comparing FP16, BF16 and PowerSGD separately. Do not interpret a cap sweep as transport qualification; use Lab 10’s NCCL Tests measurements before making topology claims.

Only rank zero writes the private JSON. Repeat candidate and baseline at least three times in independent jobs. Use the profiling command below for a separate short run through the shared training launcher. It requires the shared course/results filesystem and traces each local torchrun child into a separate report; do not run a distributed lab through a single-GPU profiler launcher. Label CUDA/NVTX ranges on every relevant rank, and add NCCL API tracing only when the installed Nsight version supports it. The standard CUDA trace already shows NCCL device kernels.

## Practice

`labs/21_ddp_buckets.py` trains a small DDP model with selected bucket limits and communication hooks. It checks SGD updates against full-batch FP32 training and writes observed buckets, step timings, loss trajectories, and approximation errors.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" \
  slurm/21_ddp_buckets.sbatch --hook allreduce --bucket-cap-mb 1
```

## Check your results

Each new job owns `results/21_ddp_buckets/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/21_ddp_buckets/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/21_ddp_buckets/logs/$LAB_JOB_ID.out"
cat "results/21_ddp_buckets/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

The bucket capacity is a hint. Inspect actual uncompressed bucket sizes after warm-up and rebuilding before interpreting communication timing.

Inspect `trajectory` for every startup and measured step: candidate/reference global mean loss, gradient/parameter trajectory relative L2 error and `observed_buckets`. Require finite values, nonempty bucket observations and verified SGD updates. The allreduce result additionally requires `rtol=1e-5, atol=1e-6` agreement. Compression deliberately has no fabricated universal quality tolerance: `quality_status` stays pending for task convergence.

`slowest_rank_step_samples_ms` includes forward, mean loss, backward, hook work and SGD. It excludes the barrier, timing aggregation and full-batch oracle. This is an isolated-step experiment with validation gaps, not continuous training-job throughput. `ddp_measured_step`, `ddp_bucket_submit` and `full_batch_reference` distinguish those regions in a trace.

Capture every relevant rank before claiming an exposed communication tail. Compare at least three independent unprofiled jobs and record the installed NCCL and PyTorch versions alongside the existing trajectory and timing records.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Step / median (seconds) | `step.median_ms` | `s` |
| Slowest rank step samples (seconds) / case | `slowest_rank_step_samples_ms.*` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 21_ddp_buckets \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Use fresh jobs for each configuration, preserving seed, workload, warm-up and iterations. First vary only bucket cap; 0.1 MB is intentionally below a small layer's matrix size, so actual bucket sizes may exceed the hint. Keep the cap fixed before changing the hook.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" slurm/21_ddp_buckets.sbatch --hook allreduce --bucket-cap-mb 1
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" slurm/21_ddp_buckets.sbatch --hook allreduce --bucket-cap-mb 0.1
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" slurm/21_ddp_buckets.sbatch --hook fp16 --bucket-cap-mb 0.1
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" slurm/21_ddp_buckets.sbatch --hook bf16 --bucket-cap-mb 0.1
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" slurm/21_ddp_buckets.sbatch --hook powersgd --bucket-cap-mb 0.1 --power-rank 1 --power-start 2 --warmup 4
```

```bash
```

Reports are retained under a unique private `results/nsys-ddp-*` directory, one report per node. Correlate the measured-step ranges across both reports. An unavailable profiler leaves only this diagnostic gate pending.

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Do post-warm-up buckets differ from startup buckets? Does a smaller cap produce earlier communication or simply more collective overhead? Can conversion or low-rank factor work outweigh reduced transfer? PowerSGD may leave small tensors uncompressed; do not compute wire bandwidth from the raw bucket ledger. A faster configuration with growing numerical drift is a candidate for further evaluation, not an accepted optimization.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/21_ddp_buckets/logs/%j.out" \
  --error="$PWD/results/21_ddp_buckets/logs/%j.err" slurm/21_ddp_buckets.nsys.sbatch --hook allreduce --bucket-cap-mb 1
```

The native Systems command is in `slurm/21_ddp_buckets.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

For the guided comparison, keep the hook fixed while changing bucket capacity. In a separate experiment, keep capacity fixed while changing the hook. Do not change both controls in one pair.

Guided comparison: Use `--bucket-cap-mb`, `--hook` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing or non-finite gradients, or an optimizer step that changes no parameters, invalidate the run. Unequal work or rank-divergent collective order can hang; check data partition and launcher placement before tuning. Unsupported hooks must fail clearly, not silently switch algorithms. PowerSGD warm-up rejection means the requested measurement would not isolate its active mode. Tight FP32 failure requires diagnosing objective, reduction and precision differences before considering looser criteria.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Use both timeline dependencies and step time including completion of the required communication to explain a bucket choice. Then define an application-specific validation metric, allowable degradation, training horizon and independent seeds before adopting compression in a real model. Compare convergence and full-job cost as well as local step latency; this short synthetic experiment cannot establish either production outcome.
