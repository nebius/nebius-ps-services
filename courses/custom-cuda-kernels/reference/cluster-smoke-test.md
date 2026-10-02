# H100 CUDA C++ smoke test

**Hardware scope:** run local checks on the base cluster. Every `two_node`, `nccl_tests`, `fabric` or multi-node serving command below requires the separate two-eight-H100 cluster; never use the TCP base pair as fabric optimization evidence.

This is a target-qualification checklist, not the lesson execution order.
Follow the [syllabus](../SYLLABUS.md) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Complete the one-time directory preparation in the shared Lab Guide before submitting. Keep runtime evidence in those private result directories.

1. Record the immutable CUDA development image and review the site-specific
   container runner derived from `slurm/container_runner.example.sh`.
2. Stage the reviewed CUTLASS 4.6.1 source tree, then submit
   `slurm/build_and_test.sbatch`; it binds the recorded digest and source path
   to the CMake build, required CUTLASS Lab 09, SM90 binaries, and CTest.
3. Submit Lab 13 and confirm one full non-MIG H100 at compute capability 9.0.
4. Run Labs 01–09 and 11–12 through the one-node launcher.
5. Run the matching `slurm/LAB.sanitizer.sbatch memcheck` job for every required binary;
   submit the same launcher with `racecheck`, `initcheck`, or `synccheck` where
   the lab uses the relevant behavior.
6. Use Nsight Systems to locate the path, then Nsight Compute on one selected
   kernel and metric set.
7. Enable Lab 10 only after the CUDA 13.3 SM90a profile is qualified.
8. Repeat accepted performance candidates across independent trials and keep
   live status pending for any unexecuted profile.

## Build and correctness commands

Run from this course root after the cluster owner provides the reviewed
container runner, immutable `CUDA_IMAGE_DIGEST`, and approved `CUTLASS_ROOT`.
The build launcher uses a unique `build/run-<build-job-id>` directory. Set
`COURSE_BUILD_DIR` to that actual completed build directory before later jobs.

```bash
python3 tools/validate_course.py
bash slurm/build_and_test.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" slurm/build_and_test.sbatch
```

After that job completes the CMake build and passes CTest, use its privately recorded build directory:

```bash
export COURSE_BUILD_DIR='build/run-REPLACE_WITH_BUILD_JOB_ID'
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" slurm/13_h100_preflight.sbatch
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_fused_elementwise/logs/%j.out" \
  --error="$PWD/results/02_fused_elementwise/logs/%j.err" slurm/02_fused_elementwise.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_tiled_transpose/logs/%j.out" \
  --error="$PWD/results/03_tiled_transpose/logs/%j.err" slurm/03_tiled_transpose.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_reduction/logs/%j.out" \
  --error="$PWD/results/04_reduction/logs/%j.err" slurm/04_reduction.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_tiled_stencil/logs/%j.out" \
  --error="$PWD/results/05_tiled_stencil/logs/%j.err" slurm/05_tiled_stencil.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/06_divergence_tail/logs/%j.out" \
  --error="$PWD/results/06_divergence_tail/logs/%j.err" slurm/06_divergence_tail.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/07_resource_sweep/logs/%j.out" \
  --error="$PWD/results/07_resource_sweep/logs/%j.err" slurm/07_resource_sweep.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_library_epilogue/logs/%j.out" \
  --error="$PWD/results/09_library_epilogue/logs/%j.err" slurm/09_library_epilogue.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/11_residual_rmsnorm/logs/%j.out" \
  --error="$PWD/results/11_residual_rmsnorm/logs/%j.err" slurm/11_residual_rmsnorm.sbatch --workload small
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
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_tiled_transpose/logs/%j.out" \
  --error="$PWD/results/03_tiled_transpose/logs/%j.err" slurm/03_tiled_transpose.sanitizer.sbatch memcheck --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_tiled_transpose/logs/%j.out" \
  --error="$PWD/results/03_tiled_transpose/logs/%j.err" slurm/03_tiled_transpose.sanitizer.sbatch racecheck --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sanitizer.sbatch synccheck --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sanitizer.sbatch memcheck --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sanitizer.sbatch racecheck --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_tiled_transpose/logs/%j.out" \
  --error="$PWD/results/03_tiled_transpose/logs/%j.err" slurm/03_tiled_transpose.nsys.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_tiled_transpose/logs/%j.out" \
  --error="$PWD/results/03_tiled_transpose/logs/%j.err" slurm/03_tiled_transpose.ncu.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_capstone/logs/%j.out" \
  --error="$PWD/results/12_capstone/logs/%j.err" slurm/12_capstone.trials.sbatch --workload small
```

Require zero relevant sanitizer errors. A missing profiler or counter
permission leaves that evidence uncollected. Preserve uninstrumented timing
distributions separately from profiling and use at least three independent
capstone runs. Optional cluster/SM90a exercises need their own qualified build
and architecture statement; they are excluded from the core guided-hour total.

## Optional cluster configure, build, and test

[Lab 10: Qualify an optional thread-block-cluster launch](labs/10_hopper_cluster.md) owns the complete optional configure, build and targeted CTest procedure. Keep its build and image qualification separate from the required SM90 suite.
