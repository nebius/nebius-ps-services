# Lab 18: Run and interpret NVIDIA NCCL Tests on Slurm

NVIDIA NCCL Tests separates collective performance from model computation. In this lab you will launch its all-reduce benchmark on two H100 nodes, inspect the rank and version records, interpret both buffer-placement results, and compare a baseline with one justified candidate. The supplied Python code validates the benchmark output; it does not replace NCCL or implement RDMA. You will learn what each reported number can establish and what still requires separate evidence.

## Before you start

Qualify the benchmark's own MPI and NCCL libraries. Its MPI-enabled binary uses the site's Slurm integration; the course's `torchrun` launcher cannot substitute for that launch protocol.

**Theory preparation:** Read Lesson 11 for collective semantics, message-size curves and normalized bandwidth. Use Lab 17's unit reasoning, then qualify this independent benchmark's runtime and rank placement.

Complete Lab 17 and ask the cluster owner for an MPI-enabled `all_reduce_perf` built from the reviewed nccl-tests v2.20.0 source, commit `b4d5beebca8a76cf01335f724d154b9b9d394d96`. This is a researched candidate, not a build qualified by this course. The same executable and compatible CUDA/NCCL/MPI libraries must be available on both nodes. Keep its per-node binary hashes, build configuration and loaded-library identities in a private qualification record. The runner records only the local binary hash; that does not prove the remote copy matches.

If building is your responsibility, follow the tagged official README using `MPI=1` and explicit MPI_HOME, CUDA_HOME and NCCL_HOME paths in your own build directory. This uses upstream vendor code; no learner-authored CUDA C++ is added to the Optimizations course. Do not install drivers or replace system MPI as part of this lab. A non-MPI build launched twice can misleadingly run two isolated benchmarks, which is not a two-rank collective.

The site must qualify its MPI/Slurm integration. `srun --mpi=list` lists available modes, but listing PMIx does not prove that the benchmark's MPI build is compatible with it. Use the mode provided by the owner. Two processes, one thread/process and one GPU/thread produce two collective ranks. Under one-device-per-task visibility, the correct visible ordinal is zero on each node—not the cluster-wide rank number.

## Concepts and code path

The Slurm script reserves two nodes and delegates orchestration to the Python runner. The runner applies one job-local profile, starts the MPI-enabled binary with `srun`, and writes a newly created private text log. A failed process exit, timeout or invalid output prevents a successful JSON report. No output file is overwritten. The parser checks version/configuration, two distinct rank-host records, complete power-of-two sizes, enabled correctness checks, microsecond units, both buffer modes and completion markers.

Only selected metrics and non-identifying version fields enter the report; hostnames, PIDs and PCI addresses are discarded. Keep raw logs private. Do not use upstream `-J` JSON export for this course: the reviewed implementation can serialize arguments and environment entries, which may expose credentials or private configuration. The course JSON is a separate allowlisted summary, not a renamed copy of that export.

## Practice

Submit from the Optimizations course root. Replace the generic path and MPI mode with the owner's qualified values. The default maximum is 64 MiB; `--max-bytes` can extend a separately declared experiment up to 256 MiB. There is no automatic tool download or cluster repair.

```bash
umask 077
export COURSE_NCCL_TESTS=/path/to/nccl-tests/build/all_reduce_perf
export COURSE_MPI=pmix
sbatch slurm/nccl_tests.sbatch default --diagnostic --max-bytes 1048576
sbatch slurm/nccl_tests.sbatch default
sbatch slurm/nccl_tests.sbatch socket
```

The first job supplies INIT/NET/GRAPH diagnostics and is labeled non-acceptance timing. The next jobs measure without enabling verbose logs. Submit at least three independent jobs for each compared profile, alternating their order. Choose one of `gdr-off`, `ring`, `tree`, `qp1` or `qp4` only when its prerequisites and hypothesis are satisfied. Keep message sizes, warmups, iterations, rank placement and versions fixed.

The runner passes one thread and GPU per process, float/sum, correctness enabled, warm-up plus repeated iterations, and maximum-rank timing. Its parser deliberately accepts the uninstrumented table. To re-read a protected log offline, provide the exit status you actually recorded; inventing zero defeats the check:

```bash
python labs/18_nccl_tests_report.py --mode parse \
  --input /path/to/private-successful-run.log --exit-code 0 \
  --variant default --max-bytes 67108864
```

Add `--diagnostic` when parsing a diagnostic log. An offline parse validates content and records learner-supplied exit evidence; it always sets acceptance_timing false because it did not observe the launch or establish absence of instrumentation. Visible verbose NCCL logs also prevent a run from being labeled acceptance timing. Optional `-I 1` per-iteration summaries or `-U 1` tuning reports require a separately declared upstream diagnostic run and are outside this parser's format. The latter needs NCCL 2.28 or newer; richer identification requires 2.31 or newer. Do not upgrade a shared runtime merely to expose those columns.

## Check your results

Start with identity, not bandwidth. Verify two ranks, two different nodes, full H100 devices and visible device zero. Check nccl-tests version separately from nccl_headers and nccl_library; a header/library difference needs compatibility review, and matching numbers alone do not prove every node loaded the same file. Confirm the intended size range was not silently reduced, and that validation_iterations is positive.

For every size, inspect both `out_of_place` and `in_place`: `time_us`, `algbw_GBps`, `normalized_busbw_GBps` and `wrong`. The modes use separate input/output storage or reuse input storage, respectively. Require enabled correctness checking, `wrong=0` in both modes, the final zero-error check and a zero launcher exit. `wrong` counts incorrect checked elements, not network packet errors. `N/A` supplies no correctness proof.

Use [Lab 17's Practice](17_nccl_transport_sweep.md#practice) illustration of 64 MiB / 4,000 microseconds to check the units yourself. With two ranks, all-reduce busbw equals algbw because its factor is one. With four ranks the normalization would be 1.5, not proof that a NIC transferred that many additional bytes per second. The average-bandwidth footer averages across the tested sizes; it does not represent application throughput. Prefer the rows matching the application's payloads and one consistent buffer mode.

## Investigate the behavior

Read the outputs in four passes: capability, selected path, correctness, then performance. Read-only `nvidia-smi topo -m` describes local proximity; `ibv_devinfo`, `ibstat` and `rdma link show` can establish port/link context when installed. They are not substitutes for the runtime log. An IB transport may carry RoCE; an active Ethernet port does not prove RoCE use. A Socket profile intentionally selects NCCL's Socket network plugin. Default versus socket is an RDMA comparison only if diagnostics establish that default selected RDMA.

For GDR, compare default versus gdr-off only after the owner has qualified direct GPU-memory registration and the same RDMA transport is used in both. GPU-memory versus host-memory `perftest` checks can help the owner isolate that boundary; supported options depend on the installed build. The course does not launch unapproved listeners or reconfigure registration support.

For QP tuning, compare qp1 against qp4 with the same fabric, messages and splitting policy. A queue pair supplies hardware work queues; more QPs may distribute traffic across paths but cost overhead. For each representative size and mode, keep the three independent job values, compare their median and range, and investigate disagreement instead of choosing the best run. Repeat a retained candidate in application Labs 08 and 13; a synthetic improvement is not automatically a shorter training or inference step.

For example, if Ring was the promising single change, use a fresh submission shell with `NCCL_ALGO=Ring sbatch slurm/two_node.sbatch labs/13_collective_overlap.py --profile smoke`. Compare it with otherwise identical submissions without that override. These application labs do not take --variant; the NCCL setting is passed through the job environment before their communicator starts. Keep the global batch explicit for Lab 08, collect three runs per condition, and check that the application loaded the intended NCCL version and selected path.

## If something goes wrong

Two rank-zero records or repeated tables often indicate a non-MPI build or incompatible launcher. A device-ordinal error can indicate a mismatch between local rank indexing and CUDA visibility. A missing completion marker, nonzero exit, extra columns or truncated curve is rejected rather than silently summarized. Unsupported format changes require a deliberate parser/test update against the corresponding official source.

For failed connections, distinguish setup IP-interface reachability, RDMA port state, GPU-memory registration, shared-library loading and a failed participant. NCCL 2.21+ dynamically chooses RoCE GIDs; do not paste an old fixed `NCCL_IB_GID_INDEX`. Ask the owner to investigate link-rate changes, error counters, congestion, memory-registration limits or MPI integration. PFC/ECN, MTU, subnet managers, ACS/IOMMU, modules and switch routing are not job-local tuning exercises.

## Takeaways and next step

A trustworthy benchmark report needs identity, correct rank placement, validated outputs, defined units and independent repeated measurements. Its bus bandwidth is a normalization, its logs are diagnostic evidence, and its timing is collective—not application—latency. The supplied runner intentionally supports the declared two-node target only. On a larger approved system, use upstream documentation to qualify process/thread/GPU mapping and separate intra-node from inter-node experiments before extending this harness; do not infer NVLink/NVSwitch performance from these two single-GPU nodes.
