# Lab 03: Compare eager and compiled pointwise execution

Several pointwise operations can repeatedly read and write intermediate tensors. Compilation may combine that work and reduce launches or memory traffic. This lab compares the same expression in eager PyTorch and `torch.compile`, separating the first compiled call from warmed execution so you can reason about both startup cost and repeated-use benefit.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the qualified Optimizations environment on one H100 with a working compiler backend. Compilation can take longer than the measured steady-state operations; preserve that cost rather than hiding it in warm-up.

Large Tensor Core operations may already be efficient; launch reduction most often matters around small pointwise, indexing, normalization, or control-heavy regions that surround them.

H100 HBM bandwidth is high, but low-intensity chains can still saturate it. Efficient Tensor Core kernels may require layouts or alignments that justify a conversion only with sufficient reuse.

## Concepts and code path

The pointwise function combines multiplication, addition, SiLU and tanh on resident inputs. The program compiles that same function and compares complete eager and compiled outputs. It records first-call wall time separately, then measures both warmed paths with CUDA events. Inspect generated execution before claiming fusion; this experiment does not include a memory-layout sweep.

Given ten 4-microsecond kernels, each preceded by 8 microseconds of non-overlapped dispatch work, the modeled region takes 120 microseconds. Change to a validated compiled region with two 12-microsecond kernels and 16 microseconds of dispatch. Expected observation: warmed execution takes approximately 40 microseconds under this model, but the report must also include compile time, graph breaks, and the shape range that reuses the artifact.

Given a 1-GiB intermediate, separate bias and activation write 1 GiB then read it again in addition to their required input/output traffic. Change to a supported fused epilogue. Expected observation: it can eliminate 2 GiB of logical intermediate traffic and one launch if it actually combines these operations and no other consumer needs the intermediate. This is a traffic ledger, not a measured HBM saving: cache reuse, generated kernels, and transactions determine actual HBM traffic. Confirm fusion in a trace and measure selected-kernel traffic before claiming a physical bandwidth reduction.

## Practice

`labs/03_compile_fusion.py` runs the same pointwise expression eagerly and through `torch.compile`. It checks numerical agreement and records first-call compilation cost separately from warmed execution timing.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/03_compile_fusion/logs/%j.out" \
  --error="$PWD/results/03_compile_fusion/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/03_compile_fusion.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/03_compile_fusion/logs/$LAB_JOB_ID.out"
cat "results/03_compile_fusion/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `allclose`. Compare `compiled_first_call_ms`, `eager`, and `compiled` distributions. A faster warmed call may still lose for a short-lived application that pays compilation only to execute a few times.

Record graph breaks, compilation warm-up, launch count, kernel time, and end-to-end time.

Compilation is useful when it removes or fuses real overhead after the one-time compile cost is separated.

Record strides, inserted copies, kernel count, bytes moved, bandwidth, and end-to-end time.

Layout is an interface contract between operators, not a property to optimize in isolation.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Eager / median (seconds) | `eager.median_ms` | `s` |
| Compiled / median (seconds) | `compiled.median_ms` | `s` |
| Compiled first call (seconds) | `compiled_first_call_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 03_compile_fusion \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the small case to verify compilation and correctness. Repeat the large profile as a distinct element-count workload and retain the first-call measurement for each process.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/03_compile_fusion/logs/%j.out" \
  --error="$PWD/results/03_compile_fusion/logs/%j.err" slurm/single_gpu.sbatch labs/03_compile_fusion.py --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/03_compile_fusion/logs/%j.out" \
  --error="$PWD/results/03_compile_fusion/logs/%j.err" slurm/single_gpu.sbatch labs/03_compile_fusion.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Count eager intermediates conceptually, then inspect a profiler trace to establish actual launch reduction. Estimate a reuse break-even only when the per-call saving is positive, and state which startup costs your numerator includes.

Larger batches improve amortization but increase latency and memory. Compilation adds startup cost and can specialize excessively. Manual fusion reduces modularity and can increase registers, so retain the simplest layer that meets the target.

Fusion saves traffic and launches but can lengthen live ranges, increase registers, reduce occupancy, duplicate a reusable intermediate, or change numerical order. Packing helps downstream work while costing a full read/write and extra storage.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/03_compile_fusion/logs/capture-%J-%t.out" \
  --error="results/03_compile_fusion/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/03_compile_fusion/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/03_compile_fusion.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `course_measure`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/03_compile_fusion/logs/capture-%J-%t.out" \
  --error="results/03_compile_fusion/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/03_compile_fusion/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/03_compile_fusion.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare eager versus compiled execution, including compilation startup separately. Independently calculate the reuse break-even and choose compilation only for a workload that passes it.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Compiler failures or numerical disagreement block this candidate. Preserve the failing configuration instead of silently replacing the compiled path with eager execution. Very small arrays may primarily expose launch overhead rather than bandwidth.

Keep compilation outside steady-state timing. This lab uses `fullgraph=True`, so an unsupported graph break fails the candidate; in compilation modes that permit eager fallback, inspect that fallback before interpreting performance.

Calling `contiguous()` everywhere moves cost rather than eliminating it.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Compilation is a candidate with startup, correctness, and steady-state consequences. Next, compare the broader causal workload in Lab 09 and use evidence to decide whether a remaining hotspot justifies custom code.

Warm up the compiled path, verify graph coverage, and compare steady-state equivalent work.

State when simple batching is preferable to a compiler change.

Select a pipeline-wide layout and fuse only where correctness and maintainability remain clear.

Identify the producer and consumer of every expensive layout conversion.

Use the eager/compiled outputs to draw an intermediate-traffic ledger. The supplied experiment does not implement a layout sweep. Extension: build a short producer → library operation → consumer pipeline and profile where views become implicit copies. Compare retaining the producer's layout with one explicit conversion at a shared boundary; validate the final outputs, aliasing/mutation behavior and the complete pipeline time. Include the conversion cost and number of subsequent reuses in that comparison.
