# GPU Performance Optimization

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a qualified two-worker, sixteen-H100 cluster, which can also run the local labs with one-GPU allocations. The conceptual lessons here remain useful prerequisites.

The networking workshop continues in Advanced Lab 09 for the PyTorch NCCL
curve and Advanced Lab 10 for the MPI-enabled NCCL Tests benchmark.

Every submission uses `tools/submit_lab.py`; it creates private `results/<lab>/logs/<job>.out` and `.err` before calling Slurm. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Start with [shared environment setup](../README.md#how-to-set-up-the-lab) to prepare the cluster, course runtime, Nsight tools, private Grafana, and readiness checks.

Read [Using GPU performance tools](reference/performance-tools.md) before the first experiment. Every lab includes its own Grafana dashboard, local capture commands, a correctness gate, and a selected-result comparison. Install the shared tools once in shared environment setup and keep `small` and `large` as separate workload campaigns.

## Course guide

[Read the complete course](index.html) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster small runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **44 hours**, for the conceptual and local practical route; provisioning and queue time are excluded.

## Learning order

Read the conceptual lessons in numbered order: workload and measurement (1–3), local optimization (4–10), communication and scaling (11–12), library-first decisions (13), then fabric topology, GPU memory registration and rank timelines (14–16). Complete local practice on the base cluster. For distributed execution, follow the advanced course’s own lab route, which verifies topology and transport before training or serving experiments.

The [library-first lab](reference/labs/16_library_first_decision.md) retains BF16
and checks both implementations against an independent FP64 reference. Its
documented error budget accounts for intermediate BF16 rounding and rejects
invalid outputs before timing or publication.

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

Take this course after GPU Fundamentals. It teaches a general PyTorch
performance workflow before specialization in LLM training, LLM inference, or
custom CUDA kernels.

The lessons connect tool selection, measurement, memory, input pipelines,
distributed execution, and causal reporting. Use the complete
[diagnostic tooling setup](../README.md#how-to-set-up-the-lab) before profiler labs; it
defines allocation-context checks, tool ownership, permissions, evidence scope,
and safe installation boundaries.

Prepare and restore the runtime using the [shared guide](../README.md#how-to-run-the-labs).
Before submitting a job, use the qualified environment described in
[Versions and environment](VERSIONS.md), then follow the relevant gates in the
[cluster small runbook](reference/cluster-smoke-test.md). Stop if that environment
identity is unavailable. Set the submitting shell's file mask before submission;
the job's internal mask cannot protect scheduler output created earlier.

```bash
umask 077
python3 tools/submit_lab.py --lab 01_timing_basics slurm/single_gpu.sbatch labs/01_timing_basics.py --profile small
```

Use the Nsight launchers after freezing the baseline and hypothesis. Distributed transport, scaling and profiling practice now belongs to the advanced course; its setup qualifies the sixteen-H100 fabric.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

## Complete transfer pipelines

[Lab 19](reference/labs/19_h2d_pipeline.md) extends Lesson 6 with a bounded,
event-ordered H2D input ring. [Lab 20](reference/labs/20_d2h_pipeline.md) extends
Lesson 8 with output workers, pinned pooling, nonblocking copies and an egress
stream. Both check complete outputs and time the final drain. Measure unprofiled
runs independently from short NVTX captures; overlap and speed remain target
evidence, not consequences of selecting a mode.

Nsight Systems capture commands and report views are assigned per lab in `reference/observability.json` and repeated in each lab guide. Shared setup imports each course dashboard directory once; CPU-only and protocol-only results omit unrelated GPU telemetry. Explicit profiling exceptions explain which evidence to use instead. Keep captures separate from the unprofiled result pair.

The Compute launcher selects one matching kernel inside the lab's configured
NVTX range. In Lab 01, `course_measure` wraps the CUDA-event GEMM loop; its
capture excludes initialization and the earlier host-timer loops. Verify the
selected kernel and range in each report before interpreting its counters.
Lab 07 keeps exported PyTorch traces separate from external NVIDIA captures;
its Compute command selects the first `projection` kernel, which is a warmup
GEMM with the default arguments. These diagnostic runs do not provide clean
application timings.

Lab 14's compute comparison allows the intended FP32-to-BF16 mode change while
holding matrix width, input scaling and useful operations fixed. Dtype and
logical minimum I/O bytes stay fixed when repeating the same mode. Lab 15's
Compute command selects `uniform_tail_probe` inside `tail_measure`, excluding
input initialization and the one-block compilation probe. It captures only the
first measured grid; use Systems for the other grids and concurrent task sets.

Lab 19 selects the first GEMM inside `consume_batch`. Lab 20 selects GEMM names
inside `produce_output`, excluding the input-fill kernel. Filters use full
demangled names, including template arguments in generic library kernels.
Both captures skip
weight initialization and select a warmup GEMM with the default arguments.
Their counters are diagnostic; Systems evidence is required to establish
copy/compute overlap, and separate unprofiled runs provide whole-loop timings.

The `run-labs` recipes for Labs 09, 16 and 19 run each configured variant in
three independent jobs per profile, reversing variant order for the middle
repetition. Preserve all results, including slower runs. Lab 09's three rounds
within each process are additional observations, not independent processes;
its result environment identifies the GPU actually used.

## Readiness checkpoints

After Lesson 3, describe the bottleneck hypothesis and the evidence that could disprove it. After Lesson 10, explain whether idle time belongs to the local workload or scheduler. In Lesson 11, qualify topology and transport, then interpret the collective size curve before changing one setting. Only then compare one- and two-rank application execution in Lesson 12. The base-route capstone in Lesson 13 integrates local decisions; revisit it with fabric evidence after Lesson 16. The final decision must preserve correctness and include independent repeated runs.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, summarizes the teaching in **Mental model**, then provides **Where to Go Next** and a **Glossary**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Complete the profiler-backed capstone with a baseline, one controlled change, correctness checks, repeated measurements and a scoped keep/reject decision.
