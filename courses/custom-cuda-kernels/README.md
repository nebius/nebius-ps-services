# Custom CUDA Kernels for GPU Optimization

## Course guide

[Read the complete course](index.html) ·
[Syllabus](SYLLABUS.md) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster smoke runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **36 hours**.

## Learning order

Begin with the library-first decision and a reproducible SM90 build. Write one correct vector kernel, learn validation/profiling, then build fusion, memory and reduction skills. Establish resource limits before diagnosing tails, combine mechanisms in pipelines and RMSNorm, and complete acceptance before optional Hopper and Tile branches.

Use the [lesson-by-lesson syllabus](SYLLABUS.md) for the current activity and
readiness checkpoints. Lab numbers identify files; follow lesson order rather
than running every lab numerically. Previews and optional branches are labeled.

## Getting started

The HTML course keeps diagrams beside the relevant explanation in a wide,
responsive layout. Each diagram fits the page and retains a caption and
accessible SVG description. Each lab explains setup, concepts, code structure,
supported experiments, result checks, investigation, troubleshooting, and
takeaways, with links to its related lessons. Use the side-panel contents to
jump between topics.

Take GPU Fundamentals and GPU Performance Optimization first. The required
course path uses CUDA C++20, CMake, explicit SM90 compilation, one full H100,
and library references. No Python learner examples are used in this course.

Each lesson connects its prerequisite GPU concepts to a complete explanation
of purpose, mechanism, H100 constraints, worked reasoning, evidence, and
maintenance trade-offs.

The direct commands below are for an already allocated H100 shell inside the
qualified CUDA development image—not a login node or an ordinary local shell.
Follow [Versions and environment](VERSIONS.md) and the
[cluster smoke runbook](reference/cluster-smoke-test.md) to qualify the image
and CUTLASS source first. No CUDA build or target run is implied by source checks.

```bash
cmake -S . -B build -DCMAKE_CUDA_ARCHITECTURES=90 \
  -DCOURSE_ENABLE_CUTLASS=ON \
  -DCUTLASS_ROOT=/path/to/reviewed-cutlass-4.6.1
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

Alternatively, submit the build from the course root through the reviewed
container runner. Set the submitting shell's file mask before Slurm creates
its output; the mask inside the job starts too late for those files.

```bash
umask 077
CUDA_IMAGE_DIGEST='docker://REGISTRY/IMAGE@sha256:DIGEST' \
CUTLASS_ROOT=/shared/reviewed-cutlass-4.6.1 \
COURSE_CONTAINER_RUNNER="$PWD/slurm/container_runner.example.sh" \
sbatch slurm/build_and_test.sbatch
```

The example runner uses Apptainer. Review and adapt it to the site's container
runtime, shared filesystem, and registry authentication; every launcher passes
the immutable digest to that runner rather than merely checking a string.

The optional Hopper-accelerated target is disabled by default. Use the
[separate cluster configure/build/test procedure](reference/cluster-smoke-test.md)
after recording and qualifying an immutable CUDA 13.3 development-image digest.
Keep its build directory separate and bind later launchers to that same image;
setting a build path does not select the runtime environment.

Lab 09 includes a required source-pinned CUTLASS 4.6.1 fused bias/ReLU
epilogue. The standard build and Slurm acceptance path enable it explicitly;
review and stage that exact public release before configuring:

```bash
cmake -S . -B build-cutlass \
  -DCMAKE_CUDA_ARCHITECTURES=90 \
  -DCOURSE_ENABLE_CUTLASS=ON \
  -DCUTLASS_ROOT=/path/to/cutlass-4.6.1
```

The course does not download CUTLASS or call header availability a successful
kernel run. CUTLASS is enabled by default, so configuration fails when the
reviewed source is missing. An explicit `COURSE_ENABLE_CUTLASS=OFF` inspection
build may compile the cuBLAS path, but Lab 09 and CTest then fail intentionally;
it is never acceptance evidence. Required acceptance includes CUTLASS
compilation and CTest; profiler and timing evidence remain separate.

The simple lab executables accept no arguments for the full profile, or
`--smoke` for the small profile. Run `--help` or `-h` alone for usage. Unknown
or extra arguments are rejected before CUDA activation, so a misspelled smoke
flag cannot silently launch the full workload. Labs 08 and 12 additionally
validate their documented work-count and variant-order options. The shared
`labs/arguments.hpp` handles host-only argument checks; `labs/common.cuh` owns
device checks, timing, allocation and numerical comparisons.

CUDA compilation, CTest, Compute Sanitizer, Nsight, and performance remain
pending until they run on the Linux/H100 target.

The sanitizer launcher takes an explicit allow-listed tool, for example
`sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR:?set the completed build directory}/03_tiled_transpose"`; use `memcheck`,
`racecheck`, `initcheck`, or `synccheck` according to the failure hypothesis.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.
