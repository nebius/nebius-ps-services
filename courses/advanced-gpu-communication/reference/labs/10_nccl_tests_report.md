# Lab 10: Run and interpret NVIDIA NCCL Tests on Slurm

NVIDIA NCCL Tests separates collective performance from model computation. In this lab you will launch its all-reduce benchmark on two H100 nodes, inspect the rank and version records, interpret both buffer-placement results, and compare a baseline with one justified candidate. The supplied Python code validates the benchmark output; it does not replace NCCL or implement RDMA. You will learn what each reported number can establish and what still requires separate evidence.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Qualify the benchmark's own MPI and NCCL libraries. Its MPI-enabled binary uses the site's Slurm integration; the course's `torchrun` launcher cannot substitute for that launch protocol.

Use an MPI-enabled `all_reduce_perf` built from the reviewed nccl-tests v2.20.0 source, commit `b4d5beebca8a76cf01335f724d154b9b9d394d96`. This is a researched candidate, not a build qualified by this course. The same executable and compatible CUDA/NCCL/MPI libraries must be available on both nodes. Keep its per-node binary hashes, build configuration and loaded-library identities in a private qualification record. The runner records only the local binary hash; that does not prove the remote copy matches.

The build below prepares the vendor benchmark with `MPI=1` and explicit MPI_HOME, CUDA_HOME and NCCL_HOME paths. Verify that qualified build is the one used here. Do not install drivers or replace system MPI as part of this lab. A non-MPI build launched twice can misleadingly run two isolated benchmarks, which is not one collective spanning the allocation.

The site must qualify its MPI/Slurm integration. `srun --mpi=list` lists available modes, but listing PMIx does not prove that the benchmark's MPI build is compatible with it. Use the mode provided by the owner. Eight processes per node, one thread/process and one GPU/thread produce eight ranks on one node or sixteen across two nodes. Under one-device-per-task visibility, the correct visible ordinal is zero on each node—not the cluster-wide rank number.

The installer records pinned revisions and hashes. Build the MPI-enabled NCCL Tests binary with the owner’s library paths and Slurm MPI mode:

```bash
git clone https://github.com/NVIDIA/nccl-tests.git "$COURSE_TOOLS/fabric/nccl-tests"
git -C "$COURSE_TOOLS/fabric/nccl-tests" checkout --detach b4d5beebca8a76cf01335f724d154b9b9d394d96
make -C "$COURSE_TOOLS/fabric/nccl-tests" -j4 MPI=1 \
  MPI_HOME="${MPI_HOME:?qualified MPI path}" CUDA_HOME="${CUDA_HOME:?CUDA path}" \
  NCCL_HOME="${NCCL_HOME:?qualified NCCL path}"
export COURSE_NCCL_TESTS="$COURSE_TOOLS/fabric/nccl-tests/build/all_reduce_perf"
export COURSE_MPI="${COURSE_MPI:?owner-qualified Slurm MPI mode}"
```

Retain exports privately. Containers need the same shared paths, tools, libraries, GPUs and IB devices. Resolve counter permissions with the owner.

Save these settings without overwriting the shared Python runtime:

```bash
declare -p COURSE_NCCL_TESTS COURSE_MPI >> "$HOME/courses/.runtime/$COURSE.sh"
```

## Concepts and code path

The runner passes one thread and GPU per process, float/sum, correctness enabled, warm-up plus repeated iterations, and maximum-rank timing. Its parser deliberately accepts the uninstrumented table. To re-read a protected log offline, provide the exit status you actually recorded; inventing zero defeats the check:

The Slurm script reserves two nodes and delegates orchestration to the Python runner. The runner applies one job-local profile, starts the MPI-enabled binary with `srun`, and writes a newly created private text log. It sets `NCCL_TESTS_DEVICE=0` in the benchmark child's environment because Slurm exposes one assigned GPU per task. Without that explicit selection, the benchmark uses the MPI local rank and ranks above zero try to select an invisible device. Leave inherited device overrides unset; the runner owns this binding and preserves Slurm's GPU isolation. A failed process exit, timeout or invalid output prevents a successful JSON report. No output file is overwritten. The parser checks version/configuration, eight distinct GPUs per node and the expected eight or sixteen rank records, complete power-of-two sizes, enabled correctness checks, microsecond units, both buffer modes and completion markers.

For profiler captures, Slurm writes each rank's stdout and stderr to separate files in `10_nccl_tests_report-run-<run-id>.rank-logs/` beside the private log. The runner combines them only after all tasks exit successfully, preserving complete lines despite concurrent profiler progress. Missing rank logs, oversized logs and runtime warnings still fail validation. Retain the rank files when investigating a failed capture.

The MPI step also uses Slurm's `--gres-flags=allow-task-sharing`: tasks may access peer GPU devices within the same job allocation for CUDA IPC, while `CUDA_VISIBLE_DEVICES` retains each task's one-device binding. Device cgroup isolation without this flag can reject a peer-memory import even when every rank correctly selects device zero. This is step-local access within the allocation, not permission to use another job's GPUs; no cluster cgroup policy is changed.

Only selected metrics and non-identifying version fields enter the report; hostnames, PIDs and PCI addresses are discarded. Keep raw logs private. Do not use upstream `-J` JSON export for this course: the reviewed implementation can serialize arguments and environment entries, which may expose credentials or private configuration. The course JSON is a separate allowlisted summary, not a renamed copy of that export.

## Practice

`labs/10_nccl_tests_report.py` runs or parses an MPI nccl-tests all-reduce sweep. It validates version, rank placement, complete message sizes, in-place/out-of-place correctness, and completion markers before writing structured latency and bandwidth results.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_nccl_tests_report/logs/%j.out" \
  --error="$PWD/results/10_nccl_tests_report/logs/%j.err" \
  slurm/10_nccl_tests_report.sbatch \
  default
```

## Check your results

Each new job owns `results/10_nccl_tests_report/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/10_nccl_tests_report/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Use the result inspector and publisher for the clean timing jobs. Diagnostic
and profiler artifacts deliberately exclude acceptance timing and are rejected
by those helpers; inspect their private logs, correctness rows and reports
separately. Retain diagnostic transport selection before interpreting a
default-versus-Socket timing comparison as an RDMA comparison.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/10_nccl_tests_report/logs/$LAB_JOB_ID.out"
cat "results/10_nccl_tests_report/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Start with identity, not bandwidth. Verify sixteen ranks across two nodes (or eight on one node), eight distinct full H100 devices per node and visible device zero for each task. Check nccl-tests version separately from nccl_headers and nccl_library; a header/library difference needs compatibility review, and matching numbers alone do not prove every node loaded the same file. Confirm the intended size range was not silently reduced, and that validation_iterations is positive.

For every size, inspect both `out_of_place` and `in_place`: `time_us`, `algbw_GBps`, `normalized_busbw_GBps` and `wrong`. The modes use separate input/output storage or reuse input storage, respectively. Require enabled correctness checking, `wrong=0` in both modes, the final zero-error check and a zero launcher exit. `wrong` counts incorrect checked elements, not network packet errors. `N/A` supplies no correctness proof.

For example, 64 MiB / 4,000 microseconds is 67,108,864 bytes / 0.004 seconds, or about 16.78 GB/s; these are illustrative values. All-reduce busbw is algbw multiplied by 2(N−1)/N: 1.75 for eight ranks and 1.875 for sixteen. This normalization is not proof that a NIC transferred that many bytes per second. The average-bandwidth footer averages across the tested sizes; it does not represent application throughput. Prefer the rows matching the application's payloads and one consistent buffer mode.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Rows / case / out of place / time (seconds) | `rows.*.out_of_place.time_us` | `s` |
| Rows / case / out of place / algbw GBps | `rows.*.out_of_place.algbw_GBps` | `Bps` |
| Rows / case / in place / time (seconds) | `rows.*.in_place.time_us` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 10_nccl_tests_report \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Submit from the `advanced-gpu-communication` course directory. Replace the generic path and MPI mode with the owner's qualified values. The default maximum is 64 MiB; `--max-bytes` can extend a separately declared experiment up to 256 MiB. There is no automatic tool download or cluster repair.

Soperator's NCCL-debug SPANK plugin can override `NCCL_DEBUG` at step launch.
The timing shell below keeps both controls at `WARN`; the separate diagnostic
submission sets `SNCCLD_LOG_LEVEL=INFO`, matching the runner's `--diagnostic`
setting. Confirm the plugin's `--nccld-log-level` support in `srun --help`.

```bash
export COURSE_NCCL_TESTS=/path/to/nccl-tests/build/all_reduce_perf
export COURSE_MPI=pmix
export NCCL_DEBUG=WARN
export SNCCLD_LOG_LEVEL=WARN
SNCCLD_LOG_LEVEL=INFO sbatch --chdir="$PWD" \
  --output="$PWD/results/10_nccl_tests_report/logs/%j.out" \
  --error="$PWD/results/10_nccl_tests_report/logs/%j.err" slurm/10_nccl_tests_report.sbatch default --diagnostic --max-bytes 1048576
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_nccl_tests_report/logs/%j.out" \
  --error="$PWD/results/10_nccl_tests_report/logs/%j.err" slurm/10_nccl_tests_report.sbatch default
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_nccl_tests_report/logs/%j.out" \
  --error="$PWD/results/10_nccl_tests_report/logs/%j.err" slurm/10_nccl_tests_report.sbatch socket
```

The first job supplies INIT/NET/GRAPH diagnostics and is labeled non-acceptance timing. The next jobs measure without enabling verbose logs. Submit at least three independent jobs for each compared profile, alternating their order. Choose one of `gdr-off`, `ring`, `tree`, `qp1` or `qp4` only when its prerequisites and hypothesis are satisfied. Keep message sizes, warmups, iterations, rank placement and versions fixed.

For an offline parse, supply the recorded exit status. Parsed logs are diagnostic only.

```bash
"$COURSE_PYTHON" labs/10_nccl_tests_report.py --mode parse \
  --input /path/to/private-successful-run.log --exit-code 0 \
  --variant default --max-bytes 67108864
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Read the outputs in four passes: capability, selected path, correctness, then performance. Read-only `nvidia-smi topo -m` describes local proximity; `ibv_devinfo`, `ibstat` and `rdma link show` can establish port/link context when installed. They are not substitutes for the runtime log. An IB transport may carry RoCE; an active Ethernet port does not prove RoCE use. A Socket profile intentionally selects NCCL's Socket network plugin. Default versus socket is an RDMA comparison only if diagnostics establish that default selected RDMA.

For GDR, compare default versus gdr-off only after the owner has qualified direct GPU-memory registration and the same RDMA transport is used in both. GPU-memory versus host-memory `perftest` checks can help the owner isolate that boundary; supported options depend on the installed build. The course does not launch unapproved listeners or reconfigure registration support.

For QP tuning, compare qp1 against qp4 with the same fabric, messages and splitting policy. A queue pair supplies hardware work queues; more QPs may distribute traffic across paths but cost overhead. For each representative size and mode, keep the three independent job values, compare their median and range, and investigate disagreement instead of choosing the best run. Repeat a retained candidate in application Labs 08 and 13; a synthetic improvement is not automatically a shorter training or inference step.

For example, if Ring was the promising single change, use a fresh submission shell with:

```bash
NCCL_ALGO=Ring sbatch --chdir="$PWD" \
  --output="$PWD/results/13_collective_overlap/logs/%j.out" \
  --error="$PWD/results/13_collective_overlap/logs/%j.err" slurm/13_collective_overlap.sbatch --workload small
```

 Compare it with otherwise identical submissions without that override. These application labs do not take --variant; the NCCL setting is passed through the job environment before their communicator starts. Keep the global batch explicit for Lab 12, collect three runs per condition, and check that the application loaded the intended NCCL version and selected path.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_nccl_tests_report/logs/%j.out" \
  --error="$PWD/results/10_nccl_tests_report/logs/%j.err" slurm/10_nccl_tests_report.nsys.sbatch default
```

The native Systems command is in `slurm/10_nccl_tests_report.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Change the positional variant immediately after `slurm/10_nccl_tests_report.sbatch`, such as `default` to `socket`, in the Practice commands. The launcher passes that selection to the Python runner's `--variant` option. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional qualified variant, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Two rank-zero records or repeated tables often indicate a non-MPI build or incompatible launcher. A device-ordinal error can indicate a mismatch between local rank indexing and CUDA visibility. A missing completion marker, nonzero exit, extra columns or truncated curve is rejected rather than silently summarized. Unsupported format changes require a deliberate parser/test update against the corresponding official source.

For failed connections, distinguish setup IP-interface reachability, RDMA port state, GPU-memory registration, shared-library loading and a failed participant. NCCL 2.21+ dynamically chooses RoCE GIDs; do not paste an old fixed `NCCL_IB_GID_INDEX`. Ask the owner to investigate link-rate changes, error counters, congestion, memory-registration limits or MPI integration. PFC/ECN, MTU, subnet managers, ACS/IOMMU, modules and switch routing are not job-local tuning exercises.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

A trustworthy benchmark report needs identity, correct rank placement, validated outputs, defined units and independent repeated measurements. Its bus bandwidth is a normalization, its logs are diagnostic evidence, and its timing is collective—not application—latency. The runner supports one or two eight-H100 nodes. To establish an intra-node reference, submit with `--nodes=1` before `slurm/10_nccl_tests_report.sbatch`; the same launcher then runs eight ranks. Keep that separate from the sixteen-rank inter-node campaign: changing rank count changes the workload and cannot establish a like-for-like tuning speedup.

Add `--diagnostic` when parsing a diagnostic log. An offline parse validates content and records learner-supplied exit evidence; it always sets acceptance_timing false because it did not observe the launch or establish absence of instrumentation. Visible verbose NCCL logs also prevent a run from being labeled acceptance timing. Optional `-I 1` per-iteration summaries or `-U 1` tuning reports require a separately declared upstream diagnostic run and are outside this parser's format. The latter needs NCCL 2.28 or newer; richer identification requires 2.31 or newer. Do not upgrade a shared runtime merely to expose those columns.
