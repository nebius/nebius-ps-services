# Custom CUDA Kernels for GPU Optimization

For a tensor-reading refresher, use [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html), a concise visual course with commented examples and no labs.

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

This course has no multi-node executable labs. Hopper thread-block clusters in Lab 10 operate inside one GPU and remain in the base route. Use [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html) for cross-GPU practice.

Lab preparation creates private log directories before any `sbatch` submission; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Start with [Lab Guide](../lab-guide.html#lab-preparation-scripts) to prepare the cluster, course runtime, Nsight tools, private Grafana, and readiness checks.

Read [GPU Performance Tools](../gpu-performance-tools/index.html) before the first experiment. Every lab includes its own Grafana dashboard, local capture commands, a correctness gate, and a selected-result comparison. Keep `small` and `large` as separate workload campaigns.

## Runtime preparation

Use the Lab Guide linked above for CUDA preparation. Then follow
[Lab 13](reference/labs/13_h100_preflight.md) to qualify the prepared native
toolkit and builds on a worker before running other kernel labs.

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

CUDA preparation provides managed native CUDA 13.3.0 and CUTLASS 4.6.1,
with SM90 binaries and a separate Lab 10 SM90a target compiled without a GPU.
These installations are not GPU qualified until the lab checks pass. Native
launchers automatically select the recorded toolkit and build path. Begin with
[Lab 13](reference/labs/13_h100_preflight.md) to establish actual device execution.

CMake configuration and compilation are preparation; CTest executes the kernels.
To deliberately rebuild and run CTest inside a GPU allocation, use the optional
container teaching job from this course directory, after selecting that optional exercise in the Lab Guide:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" slurm/build_and_test.sbatch
```

That job keeps its own build directory. It does not replace setup's prepared
runtime or qualify sanitizer/profiler behavior. CUTLASS is enabled by default;
Lab 09 requires its fused bias/ReLU path. A source-inspection build with
`COURSE_ENABLE_CUTLASS=OFF` is not acceptance evidence.

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
