# Lab 12: Assemble a kernel acceptance report

A custom kernel is ready only when its correctness, memory safety, performance mechanism, and application impact are understood. This capstone provides a two-kernel affine-plus-tanh baseline and a fused candidate with matched inputs. You will run independent counterbalanced trials and assemble the evidence needed to accept or reject the candidate without confusing a kernel-only win with production readiness.

Both paths compute `tanh(1.25*x + 0.5)` for each input element. An affine transformation scales a value and adds an offset; here it produces 1.25*x + 0.5. The hyperbolic tangent, tanh, is a smooth nonlinear function with mathematical outputs between -1 and 1, approaching those limits for large magnitudes. For x=0, the affine result is 0.5 and tanh(0.5) is approximately 0.4621. The optimization keeps this expression and changes whether its intermediate is written by one kernel and read by another.

## Before you start

**Theory preparation:** Read Lesson 14 for a complete acceptance report and Lesson 5 for fusion and rounding. The affine/tanh fixture, reference and byte accounting are explained below.

Use the completed SM90 build and one H100. Prepare the benchmark worksheet with the exact expression, FP32 tolerance, workload sizes, and acceptance metric. Sanitizer and profiler checks are separate required activities, not implied by the timing program.

Acceptance applies to the measured full non-MIG H100, toolchain, and workload only. Requalify after meaningful CUDA, driver, framework, library, model-shape, or compiler changes.

## Concepts and code path

The baseline writes an affine intermediate and launches tanh separately; the candidate computes both before its final write. The host constructs a CPU reference, compares both full outputs, and times variants in an explicitly requested order. Each invocation is one process. The campaign launcher runs three processes with alternating order and aggregates the scoped decision.

## Practice

Given a kernel accounting for 2 percent of request latency, consider a candidate with identical work and outputs. Change only its implementation to reduce that kernel's time by 20 percent. This reduces total time by only 0.02 × 0.20 = 0.004, or 0.4 percent, before integration overhead. Speedup is then 1/0.996, about 1.004×. For a fused region accounting for 35 percent of request latency, a 15-percent reduction in its time would reduce total time by 5.25 percent before overhead. Expected observation: the larger candidate can matter, but only if reference tests, sanitizers, supported-shape trials, and full application distributions all pass.

Run Lab 12's supplied separate-kernel versus fused-kernel comparison with `sbatch slurm/capstone_three_trials.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/12_capstone" --smoke`. Use the directory recorded by the successful build job. The launcher runs three fresh processes with alternating order and an aggregated decision. Extension: implement another justified technique, rebuild into a fresh directory, and repeat correctness, sanitizer, profiler and timing gates; the supplied lab does not implement that new candidate for you.

Run memory checking with an explicit order, then the three-trial campaign. Keep profiler reports separate and choose a fresh private output prefix when following the runbook's profiler commands.

```bash
umask 077
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR:?set the completed build directory}/12_capstone" --smoke --variant-order baseline-first
sbatch slurm/capstone_three_trials.sbatch "${COURSE_BUILD_DIR}/12_capstone" --smoke
```

### Optional packaging extension after core acceptance

A release is an integration exercise after performance acceptance. Use Lab 01's vector addition as a small packaging extension: separate the device implementation and its launch wrapper from the demonstration program. Expose a header describing input/output device pointers, element count, supported FP32 contiguous layout, stream and error behavior. Validate zero length, bounds, device ownership and permitted aliasing before choosing launch geometry. State whether return means submitted or completed; for an asynchronous API, the consumer must obey the documented stream dependency before reading results. Do not hide device-wide synchronization inside a reusable wrapper unless the contract requires it.

Create a source package with a public include directory, implementation sources, build configuration, license, tests and a minimal consumer. A separate library target owns the wrapper; the consumer links that target and reproduces the expected result without including implementation files directly. Test a clean build against the stated C++/CUDA/toolkit/architecture matrix and run edge-case correctness and Compute Sanitizer on the target. If distributing a compiled library, publish matching headers, versioned interface, linked-runtime expectations, supported GPU code and artifact checksums. Build-time support is not runtime validation.

For framework integration, document device/dtype/shape inference and operator registration, define gradients if training uses the operation, and test the framework's compiler and dispatch behavior. Choose an explicit supported path, not silent unsupported-device fallbacks. Release only public-safe source and reviewed evidence; exclude raw profiler traces, private model inputs and local paths. The existing course CMake targets are runnable executables, not an installed reusable library. This packaging exercise must be implemented and consumer-tested separately before claiming a distributable package; no course command uploads it for you.

## Check your results

Require baseline and candidate FP32 reference agreement, no relevant sanitizer errors, all three independent records, and consistent workload identity. Inspect variant order and distributions. The executable explicitly leaves sanitizer, profiler, and end-to-end integration claims pending until separately demonstrated.

Each trial must provide exactly one element count, variant order, baseline
median and candidate median. Counts must match across trials, timing values
and the derived ratio must be finite and positive, and order must match the
launcher. Missing, duplicate or malformed records fail aggregation.

Retain contract, tests, sanitizer output, compiler resources, profiler counters, sample distributions, integration result, portability, and decision.

Rejecting the kernel is a successful engineering outcome when evidence does not justify ownership.

## Investigate the behavior

Use a selected-kernel profile to explain removed intermediate traffic and launch cost. Does the result survive order reversal? Estimate the maximum application gain from the hotspot's fraction of total runtime before prioritizing integration.

A candidate can be rejected despite a kernel win when contribution is small, variance rises, fallback dominates, or maintenance cost exceeds value. A slower kernel might still be kept if it reduces memory use enough for the application to fit in the available memory and still meets the declared performance requirement.

## If something goes wrong

Missing order, failed reference checks, or sanitizer findings reject the trial. Do not substitute instrumented timing for acceptance data or combine records from different builds. Preserve a slower candidate as a legitimate outcome.

Avoid publishing only the fastest sample or omitting shapes where the candidate loses.

## Takeaways and next step

Deliver the reference contract, build identity, correctness/sanitizer results, all timing trials, mechanism, and remaining integration limits. Apply the same acceptance structure to a new kernel only after library-first options have been evaluated.

Report the operating envelope, confidence, fallback, and maintenance owner with the scoped decision.

State the strongest claim and the rollback condition.
