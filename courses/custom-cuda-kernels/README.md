# Custom CUDA Kernels for GPU Optimization

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

This course has no multi-node executable labs. Hopper thread-block clusters in Lab 10 operate inside one GPU and remain in the base route. Use [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html) for cross-GPU practice.

Each native submission block prepares private log directories before calling `sbatch`; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Start with [shared environment setup](../README.md#how-to-set-up-the-lab) to prepare the cluster, course runtime, Nsight tools, private Grafana, and readiness checks.

Read [Using GPU performance tools](../gpu-performance-tools/index.html) before the first experiment. Every lab includes its own Grafana dashboard, local capture commands, a correctness gate, and a selected-result comparison. Install the shared tools once in shared environment setup and keep `small` and `large` as separate workload campaigns.

## Runtime preparation

After shared setup, use [Lab 13](reference/labs/13_h100_preflight.md) to prepare
and build the CUDA image/CUTLASS runtime before running other kernel labs.

## Course guide

[Read the complete course](index.html) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster small runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **36 hours**.

## Learning order

Begin with the library-first decision and a reproducible SM90 build. Write one correct vector kernel, learn validation/profiling, then build fusion, memory and reduction skills. Establish resource limits before diagnosing tails, combine mechanisms in pipelines and RMSNorm, and complete acceptance before optional Hopper and Tile branches.

Use each lesson’s Objective and Practice for the current activity; the
readiness checkpoints below help you decide when to continue. Lab numbers identify files; follow lesson order rather
than running every lab numerically. Previews and optional branches are labeled.

## Getting started

The HTML course keeps diagrams beside the relevant explanation in a wide,
responsive layout. Each diagram fits the page and retains a caption and
accessible SVG description. Each lab starts with a concise purpose and explains the needed concepts,
code structure, supported experiments, result checks, investigation,
troubleshooting and takeaways locally. Use the side-panel contents to
jump between topics.

Take GPU Fundamentals and GPU Performance Optimization first. The required
course path uses CUDA C++20, CMake, explicit SM90 compilation, one full H100,
and library references. No Python learner examples are used in this course.

Validation on the supported H200 substitute records that observed hardware.
The capstone checks all three trials use the same GPU identity and scopes its
decision to that device; H200 timings do not qualify H100 performance.

Each lesson connects its prerequisite GPU concepts to a complete explanation
of purpose, mechanism, H100 constraints, worked reasoning, evidence, and
maintenance trade-offs.

The direct commands below are for an already allocated H100 shell inside the
qualified CUDA development image—not a login node or an ordinary local shell.
Follow [Versions and environment](VERSIONS.md) and the
[cluster small runbook](reference/cluster-smoke-test.md) to qualify the image
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
CUDA_IMAGE_DIGEST='docker://REGISTRY/IMAGE@sha256:DIGEST' \
CUTLASS_ROOT=/shared/reviewed-cutlass-4.6.1 \
COURSE_CONTAINER_RUNNER="$PWD/slurm/container_runner.example.sh" \
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" slurm/build_and_test.sbatch
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
`--workload small` for the small profile. Run `--help` or `-h` alone for usage. Unknown
or extra arguments are rejected before CUDA activation, so a misspelled small
flag cannot silently launch the full workload. Labs 08 and 12 additionally
validate their documented work-count and variant-order options. The shared
`labs/arguments.hpp` handles host-only argument checks; `labs/common.cuh` owns
device checks, timing, allocation and numerical comparisons.

CUDA compilation, CTest, Compute Sanitizer, Nsight, and performance remain
pending until they run on the Linux/H100 target.

The sanitizer launcher takes an explicit allow-listed tool, for example
:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_tiled_transpose/logs/%j.out" \
  --error="$PWD/results/03_tiled_transpose/logs/%j.err" slurm/03_tiled_transpose.sanitizer.sbatch racecheck
```

Use `memcheck`,
`racecheck`, `initcheck`, or `synccheck` according to the failure hypothesis.
For Racecheck, the launcher bounds analysis workers by the allocated CPU count
and synchronizes after two queued launches to limit host tracking-memory growth.
It retains every kernel launch and the hazard checks. Sanitizer timings are
diagnostic overhead, not benchmark results.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

Nsight Systems capture commands and report views are assigned per lab in `reference/observability.json` and repeated in each lab guide. Shared setup imports each course dashboard directory once; CPU-only and protocol-only results omit unrelated GPU telemetry. Explicit profiling exceptions explain which evidence to use instead. Keep captures separate from the unprofiled result pair.

## Readiness checkpoints

After Lesson 4, demonstrate a correct edge case, understand what memcheck establishes and choose one profiling question. Revisit racecheck/synccheck when shared-memory cooperation appears. After Lesson 10, distinguish resource capacity from uneven work. Lesson 14 completes the required single-H100 path; Lessons 15–16 are optional and are not prerequisites for RMSNorm or the capstone.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, summarizes the teaching in **Mental model**, then provides **Where to Go Next** and a **Glossary**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Required acceptance is single-GPU CUDA C++20. Preserve reference tests, relevant sanitizer results, focused profiler evidence and independent uninstrumented trials. Distributed CUDA/NCCL remains outside this course.
