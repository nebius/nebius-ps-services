# Lab 05: Contrast memory-oriented and compute-oriented work

Roofline reasoning starts by counting work and data movement before looking at achieved speed. This lab provides two contrasting points: an elementwise vector expression and a matrix multiplication. You will connect their different reuse opportunities to possible bottlenecks without mistaking two measurements for a complete intensity sweep or a measured hardware ceiling.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 and record its exact variant, precision mode, and environment. Obtain any proposed bandwidth or compute ceiling independently from an appropriate specification or calibrated experiment, not from the result being evaluated.

H100 has several compute roofs by dtype and instruction path and different bandwidth ceilings by SKU and configuration. State which roof is used and whether it is theoretical or measured on the target.

## Concepts and code path

A matrix product `A[M,K] @ B[K,N]` produces `M*N` outputs, each summing `K` products. The dimensions `M` and `N` count output rows and columns; `K` is the shared reduction dimension. Reusing an input value across outputs is what makes matrix multiplication different from an elementwise operation in the traffic model.

The vector expression performs two counted operations per element and assumes twelve logical bytes: two FP32 reads and one write. Its modeled intensity is `2/12` FLOP/byte. The GEMM reuses matrix elements and uses a conventional `2*M*N*K` numerator. CUDA events time repeated warmed operations. Actual cache behavior, intermediate traffic, and Tensor Core selection require separate evidence.

Given a kernel performing 2 billion useful operations while moving 20 GB across HBM, intensity is 0.1 operation per byte. With a measured 2 TB/s bandwidth roof, its memory bound is 0.2 TOP/s. Change the algorithm to halve traffic without changing operations. Expected observation: the bandwidth-derived bound doubles from 0.2 to 0.4 TOP/s. The overall roofline is the smaller of this bound and the compute ceiling. This arithmetic does not depend on the starting measured performance; actual speedup depends on whether memory traffic was limiting execution.

## Practice

`labs/05_roofline_microbench.py` times a vector operation and BF16 matrix multiplication, reporting estimated bandwidth and arithmetic throughput. It checks finite outputs; counters are needed to establish the actual bottleneck.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_roofline_microbench/logs/%j.out" \
  --error="$PWD/results/05_roofline_microbench/logs/%j.err" \
  slurm/05_roofline_microbench.sbatch --workload small
```

## Check your results

Each new job owns `results/05_roofline_microbench/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/05_roofline_microbench/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/05_roofline_microbench/logs/$LAB_JOB_ID.out"
cat "results/05_roofline_microbench/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Inspect `bandwidth_kernel` and `gemm` distributions and their derived rates. The implementation checks only finite outputs; it does not prove numerical agreement with an independent reference. Add that reference before accepting a modified kernel or publishing an optimization claim.

Retain operations, bytes, achieved bandwidth, achieved compute rate, and the declared theoretical or measured ceilings.

Roofline is a classification model; its accuracy depends on correct byte and operation accounting.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Bandwidth kernel / timing / median (seconds) | `bandwidth_kernel.timing.median_ms` | `s` |
| Bandwidth kernel / estimated gib per s | `bandwidth_kernel.estimated_gib_per_s` | `Bps` |
| Gemm / achieved tflops | `gemm.achieved_tflops` | `FLOPS` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 05_roofline_microbench --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 05_roofline_microbench \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Each profile runs one vector case and one GEMM case. Use the larger profile as a second declared workload, not as an automatic fixed-work optimization comparison.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_roofline_microbench/logs/%j.out" \
  --error="$PWD/results/05_roofline_microbench/logs/%j.err" slurm/05_roofline_microbench.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_roofline_microbench/logs/%j.out" \
  --error="$PWD/results/05_roofline_microbench/logs/%j.err" slurm/05_roofline_microbench.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Calculate `min(compute_ceiling, bandwidth_ceiling * intensity)` with consistent units and an independently chosen ceiling. Explain why measured achieved throughput is compared with this bound rather than used to define it. Distinguish logical bytes from profiler-observed traffic.

Fusion can raise intensity by removing intermediate HBM traffic but may increase registers, reduce occupancy, or limit reuse. Recomputing a cheap value can save bytes; recomputing expensive values can move the kernel toward the compute roof and slow it.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_roofline_microbench/logs/%j.out" \
  --error="$PWD/results/05_roofline_microbench/logs/%j.err" slurm/05_roofline_microbench.nsys.sbatch --workload small
```

The native Systems command is in `slurm/05_roofline_microbench.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_roofline_microbench/logs/%j.out" \
  --error="$PWD/results/05_roofline_microbench/logs/%j.err" slurm/05_roofline_microbench.ncu.sbatch --workload small
```

The native Compute command is in `slurm/05_roofline_microbench.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the bandwidth operation with the GEMM against independently chosen roofline ceilings. Independently calculate the arithmetic intensity needed to cross the ridge point; do not call unlike operations an optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A rate above your proposed roof often indicates inconsistent units, dtype peaks, or byte conventions. Check GiB versus GB and dense versus sparse peaks before assuming hardware broke a limit.

Avoid treating peak hardware rates as guaranteed application performance or ignoring intermediate tensors.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

This is a two-point classification exercise. A genuine intensity sweep is an extension requiring parameterized reuse, explicit FLOP/byte accounting, correctness references, and a record of how the workload changes at every point.

Optimize data movement below the ridge point and execution throughput above it, then validate with profiler evidence.

Explain why fusion often helps bandwidth-bound elementwise sequences.

Use Lab 05's vector-expression and GEMM cases as two contrasting intensity points. It does not supply a continuous intensity sweep. Extension: parameterize reuse, declare FLOPs and logical bytes at each point, add an independent numerical reference, and compare achieved rates with independently established roofs before locating a crossover.
