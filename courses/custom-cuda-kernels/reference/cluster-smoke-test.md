# H100 CUDA C++ smoke test

This is a target-qualification checklist, not the lesson execution order.
Follow the [syllabus](../SYLLABUS.md) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Before submitting jobs, run `umask 077` in the submitting shell. Slurm opens
output before the job body starts, so the launcher's in-job mask does not
protect a file that already exists. Keep build output, scheduler output,
profiler reports, and local paths in the ignored private artifact directories.

1. Record the immutable CUDA development image and review the site-specific
   container runner derived from `slurm/container_runner.example.sh`.
2. Stage the reviewed CUTLASS 4.6.1 source tree, then submit
   `slurm/build_and_test.sbatch`; it binds the recorded digest and source path
   to the CMake build, required CUTLASS Lab 09, SM90 binaries, and CTest.
3. Submit Lab 00 and confirm one full non-MIG H100 at compute capability 9.0.
4. Run Labs 01–09 and 11–12 through the one-node launcher.
5. Run `slurm/sanitizer.sbatch memcheck EXECUTABLE` for every required binary;
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
umask 077
python3 tools/validate_course.py
bash slurm/build_and_test.sbatch --help
sbatch slurm/build_and_test.sbatch
```

After that job completes the CMake build and passes CTest, use its privately recorded build directory:

```bash
export COURSE_BUILD_DIR='build/run-REPLACE_WITH_BUILD_JOB_ID'
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/00_h100_preflight"
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/01_vector_add" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/02_fused_elementwise" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/04_reduction" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/05_tiled_stencil" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/06_divergence_tail" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/07_resource_sweep" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/08_async_pipeline" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/09_library_epilogue" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/11_residual_rmsnorm" --smoke
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
mkdir -p results
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
sbatch slurm/sanitizer.sbatch synccheck "${COURSE_BUILD_DIR}/08_async_pipeline" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/08_async_pipeline" --smoke
sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/08_async_pipeline" --smoke
sbatch slurm/nsys_single_gpu.sbatch results/transpose-systems "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
sbatch slurm/ncu_single_gpu.sbatch results/transpose-kernel "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
sbatch slurm/capstone_three_trials.sbatch "${COURSE_BUILD_DIR}/12_capstone" --smoke
```

Require zero relevant sanitizer errors. A missing profiler or counter
permission leaves that evidence uncollected. Preserve uninstrumented timing
distributions separately from profiling and use at least three independent
capstone runs. Optional cluster/SM90a exercises need their own qualified build
and architecture statement; they are excluded from the core guided-hour total.

## Optional cluster configure, build, and test

The standard build launcher does not enable Lab 10. For this advanced profile,
first obtain a site-approved interactive allocation with one full H100. Run
from the course root inside that allocation; the following `srun` commands
use its assigned GPU rather than executing on an unallocated login node.
Export `CUDA_IMAGE_DIGEST` with the reviewed immutable CUDA 13.3 development
image and `COURSE_CONTAINER_RUNNER` with the approved runner. Use the same
reviewed `CUTLASS_ROOT`, and set `COURSE_CLUSTER_BUILD_DIR` to a fresh, private
build directory distinct from the required SM90 build.

```bash
umask 077
: "${SLURM_JOB_ID:?obtain an approved one-H100 allocation first}"
: "${CUDA_IMAGE_DIGEST:?set the qualified immutable CUDA 13.3 image}"
: "${COURSE_CONTAINER_RUNNER:?set the reviewed container runner}"
: "${CUTLASS_ROOT:?set the reviewed CUTLASS 4.6.1 source}"
: "${COURSE_CLUSTER_BUILD_DIR:?set a fresh optional build directory}"
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  cmake -S . -B "${COURSE_CLUSTER_BUILD_DIR}" -DCMAKE_CUDA_ARCHITECTURES=90 \
  -DCOURSE_ENABLE_SM90A=ON -DCOURSE_ENABLE_CUTLASS=ON -DCUTLASS_ROOT="${CUTLASS_ROOT}"
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  cmake --build "${COURSE_CLUSTER_BUILD_DIR}" --target 10_hopper_cluster --parallel
srun --ntasks=1 --gpus-per-task=1 "${COURSE_CONTAINER_RUNNER}" "${CUDA_IMAGE_DIGEST}" \
  ctest --test-dir "${COURSE_CLUSTER_BUILD_DIR}" --output-on-failure -R '^10_hopper_cluster_smoke$'
```

Stop after any failed command. CMake keeps required targets at SM90 and gives
only Lab 10 the explicit `sm_90a` compile option. The selected CTest checks only
the cluster probe; it does not replace the standard required-target suite.
For later `single_gpu.sbatch` or `sanitizer.sbatch` submissions, export this
same optional image as `CUDA_IMAGE_DIGEST` in the submitting shell and pass the
executable under `COURSE_CLUSTER_BUILD_DIR`. A build-directory variable alone
does not choose the runtime image. Record the optional build, image, CTest,
and sanitizer evidence separately and leave it pending if unavailable.
