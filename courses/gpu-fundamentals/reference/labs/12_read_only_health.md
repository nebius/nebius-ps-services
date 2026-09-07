# Lab 12: Read GPU health and sharing signals safely

A slow result may coincide with thermal, power, error, or sharing conditions that deserve investigation. This lab collects a bounded read-only snapshot and states what cannot be inferred from it. Its purpose is operational literacy: record relevant context without changing GPU configuration or turning one coincident counter into an unsupported explanation.

## Before you start

**Theory preparation:** Read Lesson 11 for MIG partitioning, MPS process sharing, power/clock/health observations and isolation. Use Lesson 1’s preflight distinction between observed device state and measured application behavior; this lab only observes configuration.

Run on an allocated H100 with permission to read its management information. Use the [evidence and privacy guide](../evidence-security.md); raw management output can contain identifiers that do not belong in public course reports.

Query only fields allowed by the site and record whether the allocation is a full non-MIG H100, as assumed by the labs. Do not enable MIG or MPS, change clocks, reset the GPU, or run stress diagnostics as part of a performance trial.

## Concepts and code path

The program issues allow-listed management queries for available health and sharing fields, parses bounded results, and records explicit limitations. MIG concerns hardware partitioning; MPS concerns cooperative process execution; scheduler time-slicing is a separate policy. A compute-mode or MIG field cannot establish whether MPS or time-slicing is active.

## Practice

Given three benchmark trials with stable clocks and one slow trial whose clock and thermal-event timestamps overlap, temperature is a supported hypothesis. Change the evidence to an Xid from the previous day. Expected observation: it remains operational history, not a cause of today’s slowdown, unless a fresh fault or persistent state connects it to the trial.

Run Lab 12 in read-only mode, classify only what its MIG and compute-mode fields prove, and attach health observations to—not inside—the causal performance claim. Obtain MPS, time-slicing, Xid-history, and DCGM evidence only through the cluster's authorized read-only procedure.

Collect the snapshot before or after a benchmark as contextual evidence. This command does not request clock changes, error clearing, partition creation, or administrative recovery.

```bash
umask 077
python labs/12_read_only_health.py --help
sbatch slurm/single_gpu.sbatch labs/12_read_only_health.py --profile smoke
```

## Check your results

Inspect `snapshot`, `sharing_evidence`, and `configuration_changed`. Require the read-only gate. Unavailable ECC, page-retirement, thermal, or other fields remain missing evidence; they are not zeros and do not certify a healthy device.

Only recognized Enabled/Disabled values classify MIG mode. An unavailable or
unrecognized value remains unknown; even Disabled is a management-mode reading,
not proof of exclusive allocation. Confirm the visible device separately in
preflight and obtain sharing policy from the cluster owner.

Capture current allocation mode, relevant counters, clocks, power, temperature, and observation time without scheduler or host identity.

Operational state is context. Correlate it with the trial before concluding that it caused a regression.

## Investigate the behavior

Define ECC as error detection/correction, Xid as a driver-reported event class, and retired pages as memory removed from service. Ask whether a concerning observation persists across authorized readings and whether it aligns with the benchmark's time interval.

Sharing can improve fleet utilization but changes isolation, capacity, and scheduling. Health telemetry is low overhead but sampled and retrospective; absence of an alarm does not prove application correctness, and an old alarm does not prove causation.

## If something goes wrong

A permissions or unsupported-field response is a reporting limit. Ask the operator for the approved DCGM or event-history procedure. Do not elevate privileges, clear errors, or alter clocks to make the lab pass.

Reconfiguring MIG, MPS, clocks, or persistence mode during a benchmark contaminates the trial.

## Takeaways and next step

Operational evidence helps qualify a measurement but does not establish causality by itself. Attach a sanitized snapshot summary to the benchmark worksheet and keep any remediation decision with the cluster owner.

Observe first, keep configuration unchanged, and escalate administration separately when a health signal requires action.

Explain why a read-only command can still be unsafe if it triggers initialization or changes criterion-relevant state.
