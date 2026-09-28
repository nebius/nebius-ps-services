# GPU Fundamentals

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a separate two-worker, sixteen-H100 cluster. The conceptual lessons here remain useful prerequisites.

Every submission uses `tools/submit_lab.py`; it creates private `results/<lab>/logs/<job>.out` and `.err` before calling Slurm. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Start with [shared environment setup](../README.md#how-to-set-up-the-lab) to prepare the cluster, course runtime, Nsight tools, private Grafana, and readiness checks.

Start Lesson 1 with the whole H100 SXM 80 GB: SMs, L2 cache and HBM, then distinguish physical hardware from grids, blocks, warps and threads, including how block size limits residency. Open Lab 10 for readiness checks and Lab 01 for its formula, timing procedure and numerical acceptance.

Read [Using GPU performance tools](reference/performance-tools.md) before the first experiment. Its worked diagrams show how to read Systems timelines, Compute reports and Grafana panels; a short PyTorch example explains NVTX markers and ranges. Every lab includes its own Grafana dashboard, local capture commands, a correctness gate, and a selected-result comparison. Install the shared tools once in shared environment setup and keep `small` and `large` as separate workload campaigns.

## Course guide

[Read the complete course](index.html) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster small runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **21 hours**.

## Learning order

Start with CPU/GPU cooperation and trustworthy timing, then identify the software stack and H100 execution hierarchy. Define memory resources before SIMT and occupancy, connect access patterns and precision to roofline, and finish with read-only health, networking layers and bounded two-node communication. Learn GPU/NIC attachment, NVLink/NVSwitch, InfiniBand/RoCE, RDMA and GPUDirect RDMA before interpreting NCCL collectives.

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

Start here. The course builds the mental model used by every later performance
course. Read `COURSE.md`, use `index.html` for the self-contained visual
version, and run labs only through the supplied Slurm launchers.

The course explains architecture, execution, timing, memory, precision, and
two-node communication through worked examples and practical labs.

## Environment

Follow [shared environment setup](../README.md#how-to-set-up-the-lab) for this course runtime and readiness checks.

## First single-GPU run

With the qualified runtime from shared environment setup, run the compatibility check before the CPU/GPU
comparison. Confirm that each job succeeds before continuing.

```bash
umask 077
python3 tools/submit_lab.py --lab 10_compatibility_stack slurm/single_gpu.sbatch labs/10_compatibility_stack.py --profile small
python3 tools/submit_lab.py --lab 01_cpu_gpu_crossover slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile small
```

## At Lesson 12

On the separately qualified fabric cluster, follow [Advanced Lab 02](../advanced-gpu-communication/reference/labs/02_collective_readiness.md), then [Advanced Lab 08](../advanced-gpu-communication/reference/labs/08_distributed_collectives.md). The advanced course owns their commands, launchers and dashboards. These activities follow the communication lesson rather than the first single-GPU session.

Cluster creation, access, profiling and Grafana are covered in the shared setup guide.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

Nsight Systems capture commands and report views are assigned per lab in `reference/observability.json` and repeated in each lab guide. Shared setup imports each course dashboard directory once; CPU-only and protocol-only results omit unrelated GPU telemetry. Explicit profiling exceptions explain which evidence to use instead. Keep captures separate from the unprofiled result pair.

## Readiness checkpoints

After Lesson 3, draw the CPU-to-device execution path and explain a trustworthy timer. After Lesson 7, distinguish memory layout, lane utilization and residency. After Lesson 10, derive a roofline bound before comparing measured performance. Finish by attaching health context and the two-node collective evidence to one scoped explanation.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, summarizes the teaching in **Mental model**, then provides **Where to Go Next** and a **Glossary**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Complete the single-GPU labs through their supplied launchers. For distributed practice, move to [Advanced Lab 02: Verify the two-node H100 platform](../advanced-gpu-communication/reference/labs/02_collective_readiness.md), then [Advanced Lab 08: Measure a two-node NCCL all-reduce](../advanced-gpu-communication/reference/labs/08_distributed_collectives.md). Keep observation, inference and unverified hypothesis separate.
