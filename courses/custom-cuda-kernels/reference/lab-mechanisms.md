# Lab mechanisms and evidence

## Lab 08: sweep compute work while holding data movement fixed

A two-stage copy pipeline alternates between two shared-memory buffers: one
holds the tile being processed while the other receives a later tile. This
walkthrough extends Lesson 11 and the [Lab 08 guide](labs/08_async_pipeline.md),
which define the cooperating thread group and its ownership protocol.

Objective: learn when useful computation can hide a tile copy and when a
two-stage pipeline merely adds synchronization. The
[async-copy lab](../labs/08_async_pipeline.cu) compares a serial path that loads and processes one shared-memory tile at a time
with a two-buffer Cooperative Groups copy pipeline.
Both apply the same recurrence to every input and are checked independently
against a CPU `std::fma` reference.

### Derive the intensity sweep

Each element is a four-byte float read once and written once. The logical
global traffic is therefore eight bytes per element. One recurrence iteration
uses one fused multiply-add (FMA): multiply two values, add a third and round
once at the end. Its multiplication and addition count as two FLOPs. With `K` iterations, logical
intensity is `2K / 8 = K/4` FLOPs per byte. The default sweep uses:

| FMA iterations per element | Logical FLOPs/element | Logical bytes/element | Logical FLOPs/byte |
| --- | --- | --- | --- |
| 0 | 0 | 8 | 0 |
| 8 | 16 | 8 | 2 |
| 32 | 64 | 8 | 8 |
| 128 | 256 | 8 | 32 |

These values exclude shared-memory traffic, loop/address instructions and
transaction padding. Caches can change actual HBM traffic. This is a useful
algorithm-level horizontal-axis estimate, not measured roofline intensity.
The recurrence has a dependency chain per lane, so instruction latency and
eligible warps also matter; this is not an independent-FMA peak-compute test.

Given four tiles, suppose load and compute require five and eight time units
per tile. Serial execution needs 52 units. A perfect two-stage schedule would
need one five-unit prime followed by four eight-unit compute stages: 37 units.
These time units belong to an idealized model; they are not measured microseconds. Change to less than
five units of compute and copy latency becomes harder to hide. Extra wait,
barrier and buffer-management costs can erase either predicted benefit.

### Run the bounded experiment

After the documented CMake build, submit:

```bash
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/08_async_pipeline" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/08_async_pipeline" --smoke --work-iterations 32
```

The first command runs all four work points. The second isolates one point for
profiling. `--work-iterations` accepts an integer from zero through 1024;
invalid, duplicate or incomplete arguments fail before GPU execution. Omit
`--smoke` for the larger bounded input. Smoke uses 4099 elements and full uses
1,048,579, so both include a partial final tile rather than testing only perfect
multiples of the 256-thread block.

1. Predict intensity for each point and record the expected limiting factor.
2. Require independent reference correctness for both variants. NaN sentinels
   expose unwritten output elements. FP32 comparisons use `rtol=1e-5` and
   `atol=1e-6`; neither variant is its own oracle.
3. Read each `sweep_point_begin`/`sweep_point_end` group separately. It records
   work, bytes, logical intensity, shared memory, and serial/pipeline timing
   distributions after five warm-ups and 20 measured samples.
4. Run Compute Sanitizer memcheck, racecheck and synccheck through the
   [sanitizer procedure](cluster-smoke-test.md), including zero-work and
   partial-tile cases. Correct values alone do not prove safe synchronization.
5. Profile a single work point to investigate waits and memory movement.
   Collect primary timing without profiler instrumentation. Keep only claims
   demonstrated on the actual H100 and pinned compiler.

### Explain stage ownership

The pipeline primes stage zero and waits before consuming it. During steady
state it copies the next tile into the alternate stage, computes the current
tile, then waits and synchronizes before rotating. The final tile drains without
issuing another copy. The serial baseline uses one 1024-byte shared tile;
the pipeline uses 2048 bytes. All block participants follow the cooperative
copy/wait protocol, including the partial-tile case. Alignment and transfer
size determine which copy implementation is selected; never infer a particular
hardware instruction just from an asynchronous API name. Consult the official
[asynchronous-copy guidance](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/async-copies.html).

### Interpretation and review

Both variants deliberately launch one block and loop through its tiles. This
isolates pipeline mechanics but does not fill all H100 SMs or measure device
HBM bandwidth. Extending it to a multi-block application introduces residency,
tail, memory-system and end-to-end questions; do not multiply a one-block timing
ratio into a whole-device speedup.

At zero work, expect to investigate pure movement and synchronization overhead,
rather than assuming the pipeline will be faster. At large work, compute dependencies
may dominate both variants. If timing regresses, retain that point and explain
it from the evidence. Review answer: overlap requires independent useful work,
safe stage reuse and affordable buffering; the default four-point sweep tests
that hypothesis instead of asserting it.
