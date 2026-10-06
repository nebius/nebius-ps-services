# Lab 05: Establish the optimization platform contract

An optimization comparison is interpretable only when the execution platform is known and functioning. This lab verifies the advanced two-node H100 fabric used by the distributed exercises, including rank placement and a known collective result. You will use the outcome as a prerequisite record, not as evidence that a workload is fast or that scaling will be efficient.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Use the approved course environment on two distinct eight-H100 nodes, with this bounded exercise using one full H100 on each. One process, called a rank, participates on each node; both ranks must use matching software. Keep placement logs private and share only sanitized summaries.

## Concepts and code path

The launcher starts distributed workers; common helpers identify ranks and devices, initialize NCCL, and collect placement information. A known all-reduce checks that the participants can exchange and sum tensors. The topology check prevents accidentally treating two processes on one node as the requested two-node experiment.

## Practice

`labs/05_transport_readiness.py` checks that two participating GPU ranks occupy distinct nodes and produce the expected NCCL sum. It writes transport readiness, backend, and rank/node counts.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_transport_readiness/logs/%j.out" \
  --error="$PWD/results/05_transport_readiness/logs/%j.err" \
  slurm/05_transport_readiness.sbatch --workload small
```

## Check your results

Each new job owns `results/05_transport_readiness/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/05_transport_readiness/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/05_transport_readiness/logs/$LAB_JOB_ID.out"
cat "results/05_transport_readiness/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `topology` and `all_reduce` correctness, world size two, and one H100 per rank. A successful import or scheduler submission without a completed result is not a passed platform check.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Correctness of selected results | `correctness` | Boolean pass |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 05_transport_readiness --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 05_transport_readiness \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Inspect command options locally, then submit through the two-node launcher. The launcher owns process startup; do not add another distributed launcher inside the lab invocation.

```bash
"$COURSE_PYTHON" labs/05_transport_readiness.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_transport_readiness/logs/%j.out" \
  --error="$PWD/results/05_transport_readiness/logs/%j.err" slurm/05_transport_readiness.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Write the invariants that must remain fixed in a later comparison: device variant, rank count, global work, dtype, environment and timed operations. Which of these would invalidate a direct baseline/candidate ratio if changed?

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_transport_readiness/logs/%j.out" \
  --error="$PWD/results/05_transport_readiness/logs/%j.err" slurm/05_transport_readiness.nsys.sbatch --workload small
```

The native Systems command is in `slurm/05_transport_readiness.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Open both rank reports. Inspect lab_workload and the CUDA/NCCL collective. Confirm placement separately from the trace; kernel duration alone does not identify the network transport. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Resolve placement failures with the scheduler owner and communication failures with the supported NCCL/network procedure. Do not tune infrastructure or substitute a different topology merely to obtain a passing result.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Preflight separates platform readiness from application performance. Preserve its result with your experiment record, then proceed to the fixed-work scaling and overlap experiments in Labs 08 and 13. Labs 01 and 02 are prerequisite timing refreshers if their measurement boundaries are not yet clear.
