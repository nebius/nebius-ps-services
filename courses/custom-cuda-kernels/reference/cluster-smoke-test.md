# H100 CUDA C++ smoke test

**Hardware scope:** every executable lab uses one full, non-MIG GPU. Qualify
the required path on H100; evidence from the supported H200 substitute applies
only to that observed hardware. Lab 10's optional SM90a thread-block clusters
operate inside one GPU and require their own qualification.

Use the [course](../index.html) for lesson order. This checklist qualifies the
local runtime and experiments; optional branches do not gate earlier lessons.

Complete the [Lab Guide](../../lab-guide.html#lab-preparation-scripts) for the course and lab number before submitting. Keep runtime evidence in those private result directories.

1. Record the prepared managed CUDA 13.3.0 toolkit and per-lab build receipts.
2. Verify the prepared CUTLASS 4.6.1 source and native SM90 binaries. Setup
   compiles without submitting GPU jobs or running CTest.
3. Submit Lab 13 and confirm one full non-MIG H100 at compute capability 9.0.
4. Run Labs 01–09 and 11–12 through the one-node launcher.
5. Run the matching `slurm/LAB.sbatch --experiment sanitizer --sanitizer memcheck` job for every required binary;
   submit the same launcher with `racecheck`, `initcheck`, or `synccheck` where
   the lab uses the relevant behavior.
6. Use Nsight Systems to locate the path, then Nsight Compute on one selected
   kernel and metric set.
7. Enable Lab 10 only after the CUDA 13.3 SM90a profile is qualified.
8. Repeat accepted performance candidates across independent trials and keep
   live status pending for any unexecuted profile.

## Build and correctness commands

Run from this course root after CUDA preparation. The optional container teaching
exercise below needs the optional selection in the Lab Guide and runs CTest in a unique
`build/run-<build-job-id>` directory. Native lab jobs use their setup-managed builds.

```bash
python3 tools/validate_course.py
bash slurm/build_and_test.sbatch --help
sbatch slurm/build_and_test.sbatch
```

Independently of the optional container exercise, run the device and correctness labs with their prepared runtime:

```bash
sbatch slurm/13_h100_preflight.sbatch
sbatch slurm/01_vector_add.sbatch --workload small
sbatch slurm/02_fused_elementwise.sbatch --workload small
sbatch slurm/03_tiled_transpose.sbatch --workload small
sbatch slurm/04_reduction.sbatch --workload small
sbatch slurm/05_tiled_stencil.sbatch --workload small
sbatch slurm/06_divergence_tail.sbatch --workload small
sbatch slurm/07_resource_sweep.sbatch --workload small
sbatch slurm/08_async_pipeline.sbatch --workload small
sbatch slurm/09_library_epilogue.sbatch --workload small
sbatch slurm/11_residual_rmsnorm.sbatch --workload small
```

Every kernel must agree with its reference for the declared shapes and
tolerances, including tails and boundary cases. A completed launch is not
proof of correct output. Lab 09 must run the real library/fused epilogue
comparison; an unavailable CUTLASS build is a blocked gate, not a substitute.

Lab 08 now runs four work points (0, 8, 32 and 128 FMAs per element).
Its smoke input has 4099 elements to exercise a partial final tile. CTest also
runs zero and maximum work explicitly. Inspect both independent CPU-reference
checks and the serial/pipeline distributions at each point. For focused
sanitizer or profiler runs append `--work-iterations 32` (or zero to exercise
the no-compute case). See [the pipeline walkthrough](lab-mechanisms.md).

## Sanitizer, profiler, and capstone commands

Choose a new private output prefix for each profiler run. The following uses
one example kernel; repeat the applicable sanitizer for every required binary.

```bash
sbatch slurm/03_tiled_transpose.sbatch --experiment sanitizer --sanitizer memcheck --workload small
sbatch slurm/03_tiled_transpose.sbatch --experiment sanitizer --sanitizer racecheck --workload small
sbatch slurm/08_async_pipeline.sbatch --experiment sanitizer --sanitizer synccheck --workload small
sbatch slurm/08_async_pipeline.sbatch --experiment sanitizer --sanitizer memcheck --workload small
sbatch slurm/08_async_pipeline.sbatch --experiment sanitizer --sanitizer racecheck --workload small
sbatch slurm/03_tiled_transpose.sbatch --profiler nsys --workload small
sbatch slurm/03_tiled_transpose.sbatch --profiler ncu --workload small
sbatch slurm/12_capstone.sbatch --experiment trials --workload small
```

Require zero relevant sanitizer errors. A missing profiler or counter
permission leaves that evidence uncollected. Preserve uninstrumented timing
distributions separately from profiling and use at least three independent
capstone runs. Optional cluster/SM90a exercises need their own qualified build
and architecture statement; they are excluded from the core guided-hour total.

## Optional cluster configure, build, and test

[Lab 10: Qualify an optional thread-block-cluster launch](labs/10_hopper_cluster.md) qualifies the setup-managed SM90a target. Keep its execution evidence and native toolkit identity separate from the required SM90 suite.
