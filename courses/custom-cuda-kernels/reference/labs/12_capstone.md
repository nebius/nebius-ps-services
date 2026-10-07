# Lab 12: Assemble a kernel acceptance report

A custom kernel is ready only when its correctness, memory safety, performance mechanism, and application impact are understood. This capstone provides a two-kernel affine-plus-tanh baseline and a fused candidate with matched inputs. You will run independent counterbalanced trials and assemble the evidence needed to accept or reject the candidate without confusing a kernel-only win with production readiness.

Both paths compute `tanh(1.25*x + 0.5)` for each input element. An affine transformation scales a value and adds an offset; here it produces 1.25*x + 0.5. The hyperbolic tangent, tanh, is a smooth nonlinear function with mathematical outputs between -1 and 1, approaching those limits for large magnitudes. For x=0, the affine result is 0.5 and tanh(0.5) is approximately 0.4621. The optimization keeps this expression and changes whether its intermediate is written by one kernel and read by another.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the completed SM90 build and one H100. Prepare the benchmark worksheet with the exact expression, FP32 tolerance, workload sizes, and acceptance metric. Sanitizer and profiler checks are separate required activities, not implied by the timing program.

Acceptance applies to the measured full non-MIG H100, toolchain, and workload only. Requalify after meaningful CUDA, driver, framework, library, model-shape, or compiler changes.

## Concepts and code path

The baseline writes an affine intermediate and launches tanh separately; the candidate computes both before its final write. The host constructs a CPU reference, compares both full outputs, and times variants in an explicitly requested order. Each invocation is one process. The campaign launcher runs three processes with alternating order and aggregates the scoped decision.

Given a kernel accounting for 2 percent of request latency, consider a candidate with identical work and outputs. Change only its implementation to reduce that kernel's time by 20 percent. This reduces total time by only 0.02 × 0.20 = 0.004, or 0.4 percent, before integration overhead. Speedup is then 1/0.996, about 1.004×. For a fused region accounting for 35 percent of request latency, a 15-percent reduction in its time would reduce total time by 5.25 percent before overhead. Expected observation: the larger candidate can matter, but only if reference tests, sanitizers, supported-shape trials, and full application distributions all pass.

A release is an integration exercise after performance acceptance. Use Lab 01's vector addition as a small packaging extension: separate the device implementation and its launch wrapper from the demonstration program. Expose a header describing input/output device pointers, element count, supported FP32 contiguous layout, stream and error behavior. Validate zero length, bounds, device ownership and permitted aliasing before choosing launch geometry. State whether return means submitted or completed; for an asynchronous API, the consumer must obey the documented stream dependency before reading results. Do not hide device-wide synchronization inside a reusable wrapper unless the contract requires it.

Create a source package with a public include directory, implementation sources, build configuration, license, tests and a minimal consumer. A separate library target owns the wrapper; the consumer links that target and reproduces the expected result without including implementation files directly. Test a clean build against the stated C++/CUDA/toolkit/architecture matrix and run edge-case correctness and Compute Sanitizer on the target. If distributing a compiled library, publish matching headers, versioned interface, linked-runtime expectations, supported GPU code and artifact checksums. Build-time support is not runtime validation.

For framework integration, document device/dtype/shape inference and operator registration, define gradients if training uses the operation, and test the framework's compiler and dispatch behavior. Choose an explicit supported path, not silent unsupported-device fallbacks. Release only public-safe source and reviewed evidence; exclude raw profiler traces, private model inputs and local paths. The existing course CMake targets are runnable executables, not an installed reusable library. This packaging exercise must be implemented and consumer-tested separately before claiming a distributable package; no course command uploads it for you.

## Practice

`labs/12_capstone.cu` compares separate affine/tanh kernels with a fused candidate in an explicit variant order. It validates both outputs against a CPU reference; the launcher aggregates three fresh-process timing trials. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" \
  slurm/12_capstone.trials.sbatch --workload small
```

## Check your results

Each new job owns `results/12_capstone/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/12_capstone/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/12_capstone/logs/$LAB_JOB_ID.out"
cat "results/12_capstone/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require baseline and candidate FP32 reference agreement, no relevant sanitizer errors, all three independent records, and consistent workload identity. Inspect variant order and distributions. The executable explicitly leaves sanitizer, profiler, and end-to-end integration claims pending until separately demonstrated.

Each trial must provide exactly one element count, variant order, baseline
median and candidate median. Counts must match across trials, timing values
and the derived ratio must be finite and positive, and order must match the
launcher. Missing, duplicate or malformed records fail aggregation.

The summary retains the observed GPU name and requires the same supported full
GPU identity in all three trials. H200 validation is scoped to the observed H200;
it does not establish H100 performance.

Retain contract, tests, sanitizer output, compiler resources, profiler counters, sample distributions, integration result, portability, and decision.

Rejecting the kernel is a successful engineering outcome when evidence does not justify ownership.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Baseline median (seconds) | `baseline_median_ms` | `s` |
| Candidate median (seconds) | `candidate_median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 12_capstone --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 12_capstone \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run memory checking with an explicit order, then the three-trial campaign. Keep all trial distributions and compare matched outputs before accepting or rejecting fusion.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" slurm/12_capstone.sanitizer.sbatch memcheck --workload small --variant-order baseline-first
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" slurm/12_capstone.trials.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Use a selected-kernel profile to explain removed intermediate traffic and launch cost. Does the result survive order reversal? Estimate the maximum application gain from the hotspot's fraction of total runtime before prioritizing integration.

A candidate can be rejected despite a kernel win when contribution is small, variance rises, fallback dominates, or maintenance cost exceeds value. A slower kernel might still be kept if it reduces memory use enough for the application to fit in the available memory and still meets the declared performance requirement.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" slurm/12_capstone.nsys.sbatch --workload small --variant-order baseline-first
```

The native Systems command is in `slurm/12_capstone.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" slurm/12_capstone.ncu.sbatch --workload small --variant-order baseline-first
```

The native Compute command is in `slurm/12_capstone.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the separate baseline with the fused candidate across three trials. Independently change threads from 256 to 128, rebuild, and repeat the complete campaign with unchanged element count and operation semantics.

After changing the CUDA source, rerun this lab’s CUDA preparation from the Lab Guide to compile a new private build with the same pinned toolkit. Then repeat the original run and capture commands.

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing order, failed reference checks, or sanitizer findings reject the trial. Do not substitute instrumented timing for acceptance data or combine records from different builds. Preserve a slower candidate as a legitimate outcome.

Avoid publishing only the fastest sample or omitting shapes where the candidate loses.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Deliver the reference contract, build identity, correctness/sanitizer results, all timing trials, mechanism, and remaining integration limits. Apply the same acceptance structure to a new kernel only after library-first options have been evaluated.

Report the operating envelope, confidence, fallback, and maintenance owner with the scoped decision.

State the strongest claim and the rollback condition.

Run Lab 12's supplied separate-kernel versus fused-kernel comparison with:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" slurm/12_capstone.trials.sbatch --workload small
```

 Use the directory recorded by the successful build job. The launcher runs three fresh processes with alternating order and an aggregated decision. Extension: implement another justified technique, rebuild into a fresh directory, and repeat correctness, sanitizer, profiler and timing gates; the supplied lab does not implement that new candidate for you.
