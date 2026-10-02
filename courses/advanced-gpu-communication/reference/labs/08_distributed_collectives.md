# Lab 08: Measure a two-node NCCL all-reduce

Distributed work cannot progress faster than its required data exchanges allow. This lab reduces a known tensor across two nodes and measures how message size affects completion time. It teaches collective semantics and payload-rate accounting on the actual inter-node path, without implying the behavior of an NVLink or NVSwitch system.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Use two distinct nodes with one H100 per process and matching software. A rank is a process's position in the communicating group. Networking, transport selection, and scheduler administration remain the cluster owner's responsibility.

With this lab selecting one participating H100 rank per eight-GPU worker on the advanced cluster, the measured path includes each GPU’s host attachment, CPU/NUMA placement, NIC path, network, and remote node. It does not exercise intra-node NVLink or NVSwitch. GPUDirect capability must be independently proven before it is claimed.

An active RDMA port establishes a hardware/network capability, not the path selected by NCCL. A runtime log naming an RDMA transport is stronger evidence of selection but still does not, by itself, establish direct GPU-memory registration. Supported DMA-BUF and peer-memory driver paths are alternative ways the platform may provide that registration. The cluster owner qualifies the platform; learners correlate that evidence with the actual application run. A missing optional capability is a clearly stated limitation, not permission to alter the cluster.

## Concepts and code path

Each rank creates a tensor whose known values make the sum predictable. The program initializes NCCL, warms the collective, resets input values between trials, and measures synchronized completion. Rank zero writes the portable result. All-reduce returns the reduced tensor to every participant; it is not merely a send from rank zero to rank one.

Given one rank completing compute in 12 milliseconds, the other in 18, and an all-reduce taking 6 after both arrive, the step is at least 24 milliseconds. Change only the fast rank’s kernel to 8 milliseconds. Expected observation: step time stays near 24 because the wait for the slower rank is unchanged; the optimization is locally real but globally hidden.

Distinguish the software operation, local GPU/network-adapter attachment, inter-node fabric and memory path. This experiment measures the existing path; it does not change network configuration.

## Practice

`labs/08_distributed_collectives.py` times a two-node NCCL all-reduce at a chosen payload size and verifies every output element equals three. It writes median latency and effective payload throughput.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/08_distributed_collectives/logs/%j.out" \
  --error="$PWD/results/08_distributed_collectives/logs/%j.err" \
  slurm/two_node.sbatch \
  labs/08_distributed_collectives.py --profile small --payload-mib 4
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/08_distributed_collectives/logs/$LAB_JOB_ID.out"
cat "results/08_distributed_collectives/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `all_reduce_sum`, then inspect `payload_mib`, `median_ms`, and `effective_payload_gib_per_s`. This lab reports rank zero's local median, not a reduced slowest-rank statistic. Its payload rate is not automatically network-link bandwidth or NCCL bus bandwidth.

Record world size, message bytes, collective, elapsed distribution, algorithmic bandwidth, GPU/CPU/NIC NUMA placement, and sanitized topology facts.

Verify GPU, CPU and NIC placement first; ask the cluster owner to resolve placement or NUMA-affinity problems before investigating NCCL tuning. Scaling evidence is valid only for the declared nodes, one-rank-per-node placement, and measured fabric; this course does not claim RDMA or direct-storage activation.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Median (seconds) | `median_ms` | `s` |
| Effective payload gib per s | `effective_payload_gib_per_s` | `Bps` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 08_distributed_collectives \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Submit separate bounded message sizes and keep the rank count unchanged. Use new result files for each run rather than overwriting the smaller-payload evidence.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/08_distributed_collectives/logs/%j.out" \
  --error="$PWD/results/08_distributed_collectives/logs/%j.err" slurm/two_node.sbatch labs/08_distributed_collectives.py --profile small --payload-mib 4
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/08_distributed_collectives/logs/%j.out" \
  --error="$PWD/results/08_distributed_collectives/logs/%j.err" slurm/two_node.sbatch labs/08_distributed_collectives.py --profile small --payload-mib 32
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Explain why small messages can be dominated by startup latency and why larger messages may expose transfer cost. Draw the operation's dependencies and distinguish application payload from the bytes moved by a particular collective algorithm.

Larger buckets amortize latency but delay overlap opportunities and require memory. Different collective algorithms favor different message sizes and topology. Tuning before fixing rank placement or skew usually optimizes the wrong problem.

Capture a separate diagnostic run:

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=8 --cpus-per-task=32 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/08_distributed_collectives/logs/capture-%J-%t.out" \
  --error="results/08_distributed_collectives/logs/capture-%J-%t.err" \
  bash slurm/capture_ranks.sh 1 \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt,nccl \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/08_distributed_collectives/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{RANK}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/08_distributed_collectives.py --profile small --payload-mib 4
```

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. Compute replay is inapplicable to the live collective; isolate a local kernel before inspecting counters.

Guided comparison: Use `--payload-mib` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `payload_mib` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A hang may be a missing rank, mismatched collective order, or a transport problem. Preserve private logs and confirm preflight before changing the experiment. Never suppress a failed exact-sum check to obtain timings.

Avoid generalizing a two-rank result to dense multi-GPU nodes or production-scale collectives.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Collective rates are meaningful only with topology, message size, and the timed operations specified. Extend measurement to the slowest rank before using it as a global-step estimate; then study communication overlap in Lab 13.

Treat the lab as a communication-mechanics baseline and preserve topology as part of benchmark identity.

State what this cluster can and cannot prove about NVLink, NVSwitch, and RDMA.
