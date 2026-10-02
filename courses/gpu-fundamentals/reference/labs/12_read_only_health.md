# Lab 12: Read GPU health and sharing signals safely

A slow result may coincide with thermal, power, error, or sharing conditions that deserve investigation. This lab collects a bounded read-only snapshot and states what cannot be inferred from it. Its purpose is operational literacy: record relevant context without changing GPU configuration or turning one coincident counter into an unsupported explanation.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Run on an allocated H100 with permission to read its management information. Use the [evidence and privacy guide](../evidence-security.md); raw management output can contain identifiers that do not belong in public course reports.

Query only fields allowed by the site and record whether the allocation is a full non-MIG H100, as assumed by the labs. Do not enable MIG or MPS, change clocks, reset the GPU, or run stress diagnostics as part of a performance trial.

## Concepts and code path

The program issues allow-listed management queries for available health and sharing fields, parses bounded results, and records explicit limitations. MIG concerns hardware partitioning; MPS concerns cooperative process execution; scheduler time-slicing is a separate policy. A compute-mode or MIG field cannot establish whether MPS or time-slicing is active.

Given three benchmark trials with stable clocks and one slow trial whose clock and thermal-event timestamps overlap, temperature is a supported hypothesis. Change the evidence to an Xid from the previous day. Expected observation: it remains operational history, not a cause of today’s slowdown, unless a fresh fault or persistent state connects it to the trial.

Run Lab 12 in read-only mode, classify only what its MIG and compute-mode fields prove, and attach health observations to—not inside—the causal performance claim. Obtain MPS, time-slicing, Xid-history, and DCGM evidence only through the cluster's authorized read-only procedure.

## Practice

`labs/12_read_only_health.py` runs bounded, read-only GPU management queries and writes health and sharing observations. It checks the expected GPU family without changing clocks, partitions, or device configuration.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/12_read_only_health/logs/%j.out" \
  --error="$PWD/results/12_read_only_health/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/12_read_only_health.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/12_read_only_health/logs/$LAB_JOB_ID.out"
cat "results/12_read_only_health/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Inspect `snapshot`, `sharing_evidence`, and `configuration_changed`. Require the read-only gate. Unavailable ECC, page-retirement, thermal, or other fields remain missing evidence; they are not zeros and do not certify a healthy device.

Only recognized Enabled/Disabled values classify MIG mode. An unavailable or
unrecognized value remains unknown; even Disabled is a management-mode reading,
not proof of exclusive allocation. Confirm the visible device separately in
preflight and obtain sharing policy from the cluster owner.

Capture current allocation mode, relevant counters, clocks, power, temperature, and observation time without scheduler or host identity.

Operational state is context. Correlate it with the trial before concluding that it caused a regression.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Correctness of selected results | `correctness` | Boolean pass |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 12_read_only_health \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Collect the snapshot before or after a benchmark as contextual evidence. This command does not request clock changes, error clearing, partition creation, or administrative recovery.

```bash
"$COURSE_PYTHON" labs/12_read_only_health.py --help
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/12_read_only_health/logs/%j.out" \
  --error="$PWD/results/12_read_only_health/logs/%j.err" slurm/single_gpu.sbatch labs/12_read_only_health.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Define ECC as error detection/correction, Xid as a driver-reported event class, and retired pages as memory removed from service. Ask whether a concerning observation persists across authorized readings and whether it aligns with the benchmark's time interval.

Sharing can improve fleet utilization but changes isolation, capacity, and scheduling. Health telemetry is low overhead but sampled and retrospective; absence of an alarm does not prove application correctness, and an old alarm does not prove causation.

**Nsight Systems: not applicable.** This health probe reads device status without launching a timed workload; use its health fields and sampled device telemetry. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

A permissions or unsupported-field response is a reporting limit. Ask the operator for the approved DCGM or event-history procedure. Do not elevate privileges, clear errors, or alter clocks to make the lab pass.

Reconfiguring MIG, MPS, clocks, or persistence mode during a benchmark contaminates the trial.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Operational evidence helps qualify a measurement but does not establish causality by itself. Attach a sanitized snapshot summary to the benchmark worksheet and keep any remediation decision with the cluster owner.

Observe first, keep configuration unchanged, and escalate administration separately when a health signal requires action.

Explain why a read-only command can still be unsafe if it triggers initialization or changes criterion-relevant state.
