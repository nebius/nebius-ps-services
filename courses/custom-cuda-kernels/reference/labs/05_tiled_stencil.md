# Lab 05: Reuse neighboring values with a shared-memory halo

Stencil operations compute each output from nearby input values, so adjacent threads can reuse much of the same data. This lab implements a one-dimensional three-point stencil with a shared tile and two halo values. You will follow interior, block-edge, and global-edge behavior while verifying the boundary condition instead of assuming every neighboring address exists.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the completed SM90 build on one H100. The small case has 1,003 elements, deliberately leaving a partial block. The boundary convention is zero outside the input domain.

The H100 unified L1/shared-memory carveout and block residency influence tile size. Profile actual reuse and resource limits rather than assuming shared memory always beats cache.

## Concepts and code path

Each thread loads its central value into shared memory. Boundary threads load the neighboring halo values where valid, and a barrier makes those loads visible before computation. The output is `0.25*left + 0.5*center + 0.25*right`. A CPU loop implements the same zero-boundary convention. Only the tiled implementation is supplied; there is no timed naive GPU baseline in this lab.

Given a 256-output block and radius one, naive code requests up to 768 input values, while a cooperative tile needs about 258 unique values before cache effects. Change `n` to 257 so the second block is partial. Expected observation: every launched thread still reaches the barrier, only valid outputs are stored, and sanitizer and correctness checks pass for the edge case.

Run Lab 05 with `--workload small` (1,003 elements) and without arguments (2²⁴ elements). Inspect interior, block-boundary and global-edge outputs; the small fixture also exercises a partial block.

## Practice

`labs/05_tiled_stencil.cu` computes a one-dimensional three-point stencil using shared-memory tiles and halo values. It verifies outputs against a CPU reference and reports timing, shared-memory usage, and halo size. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" \
  slurm/05_tiled_stencil.sbatch --workload small
```

## Check your results

Each new job owns `results/05_tiled_stencil/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/05_tiled_stencil/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/05_tiled_stencil/logs/$LAB_JOB_ID.out"
cat "results/05_tiled_stencil/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require full FP32 CPU-reference agreement and no relevant sanitizer errors. Inspect kernel distributions, `shared_bytes_per_block`, and `halo_values_per_full_block`. Correct output and timing measurements cannot establish a speedup without a measured baseline.

Retain global-load estimate, shared bytes, synchronization, time, and maximum error.

Tiling helps when reuse repays load and synchronization overhead.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Kernel median (seconds) | `kernel_median_ms` | `s` |
| Shared bytes per block | `shared_bytes_per_block` | `bytes` |
| Halo values per full block | `halo_values_per_full_block` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 05_tiled_stencil --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 05_tiled_stencil \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the small fixture with memory and race checking. The full case changes the element count but preserves the stencil and boundary rule.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" slurm/05_tiled_stencil.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" slurm/05_tiled_stencil.sanitizer.sbatch memcheck --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" slurm/05_tiled_stencil.sanitizer.sbatch racecheck --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Trace one interior point, a block-boundary point, and the final valid input. Which values are reused by neighboring threads? Why must masked threads still respect the block barrier?

Larger tiles improve halo amortization but consume shared memory and can reduce occupancy. Complex boundary logic may diverge. For little reuse, staging and barriers cost more than they save.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" slurm/05_tiled_stencil.nsys.sbatch --workload small
```

The native Systems command is in `slurm/05_tiled_stencil.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" slurm/05_tiled_stencil.ncu.sbatch --workload small
```

The native Compute command is in `slurm/05_tiled_stencil.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the supplied shared-memory stencil's 256-thread baseline with a source-edited and rebuilt 128-thread candidate at fixed elements. Explain halo overhead versus occupancy and verify both against the CPU reference. A timed direct-global stencil is an optional implementation extension, not a supplied comparison case.

After changing the CUDA source, rerun this lab’s CUDA preparation from the Lab Guide to compile a new private build with the same pinned toolkit. Then repeat the original run and capture commands.

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Errors only at block boundaries often implicate halo loading; errors at the global edge may indicate the wrong boundary convention. Inspect invalid shared/global reads before changing tile size.

A divergent early return before a block-wide barrier can deadlock.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Shared tiling makes reuse explicit but adds synchronization and halo overhead. Add a correct direct-global reference kernel as an extension, then compare the same workload and include memory-traffic evidence before claiming a performance benefit.

Keep every participating thread on the same barrier path and mask invalid data explicitly.

Identify all halo loads and boundary conditions for one block.
