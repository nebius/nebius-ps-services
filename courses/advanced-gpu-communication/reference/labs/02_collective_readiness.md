# Lab 02: Verify the two-node H100 platform

Before interpreting GPU timings, establish that the program is running on the platform you think it is. This lab checks process placement, device visibility, and communication using one rank on each of the two eight-GPU workers. It is a platform acceptance exercise, not a benchmark: a successful import on the login node cannot replace a successful allocation and collective on both compute nodes.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Use the course's approved environment on two eight-H100 workers with one participating GPU/rank on each. The launcher starts one communicating process, called a rank, on each node. Keep scheduler output private because placement diagnostics can identify infrastructure.

## Concepts and code path

The Slurm launcher creates the distributed processes. Shared helpers read rank information, bind each process to its local GPU, initialize NCCL, and exchange placement information. The lab then reduces a known tensor across ranks. World size counts participating processes; local rank selects a device within one node, so both nodes should use local rank zero here.

## Practice

`labs/02_collective_readiness.py` starts two NCCL ranks on distinct nodes and sums their GPU values. It verifies the result equals three and writes the backend, rank/node counts, and readiness checks.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_collective_readiness/logs/%j.out" \
  --error="$PWD/results/02_collective_readiness/logs/%j.err" \
  slurm/02_collective_readiness.sbatch --workload small
```

## Check your results

Each new job owns `results/02_collective_readiness/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/02_collective_readiness/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/02_collective_readiness/logs/$LAB_JOB_ID.out"
cat "results/02_collective_readiness/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require two distinct nodes, world size two, one visible non-MIG H100 per rank, and a correct all-reduce. The JSON correctness fields include `two_distinct_nodes` and `all_reduce`; the presence of a result file alone does not establish success.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Correctness of selected results | `correctness` | Boolean pass |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 02_collective_readiness \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

From this course directory, inspect the options before submitting the bounded small job. Do not run the distributed program as two unrelated manual Python processes.

```bash
"$COURSE_PYTHON" labs/02_collective_readiness.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_collective_readiness/logs/%j.out" \
  --error="$PWD/results/02_collective_readiness/logs/%j.err" slurm/02_collective_readiness.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw one process-to-device arrow on each node and one communication edge between them. Explain why successful local CUDA execution does not prove that NCCL can communicate across the inter-node network.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_collective_readiness/logs/%j.out" \
  --error="$PWD/results/02_collective_readiness/logs/%j.err" slurm/02_collective_readiness.nsys.sbatch --workload small
```

The native Systems command is in `slurm/02_collective_readiness.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Open both rank reports. Expand CUDA API, CUDA GPU and NCCL rows inside lab_workload; correlate the readiness all-reduce on the two hosts. A passing sum is correctness evidence, not throughput evidence. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A placement failure belongs to the allocation or launcher; missing CUDA belongs to the environment/device path; a collective timeout requires the cluster owner's network and NCCL investigation. Preserve the original failure and do not change cluster settings yourself.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

You now have an explicit execution topology on which the collective experiment can depend. Repeat preflight after changing the environment or placement. Lab 01 establishes the fabric topology; proceed to Lab 08’s bounded two-rank communication experiment and interpret its timing against the verified placement.
