# Lab 00: Establish the optimization platform contract

An optimization comparison is interpretable only when the execution platform is known and functioning. This lab verifies the two-node H100 setup used by the distributed exercises, including rank placement and a known collective result. You will use the outcome as a prerequisite record, not as evidence that a workload is fast or that scaling will be efficient.

## Before you start

**Theory preparation:** Read Lesson 11 and Fundamentals Lesson 12 for topology, process groups, rank placement and known-value all-reduce checks. Use the timing contract from Lesson 2. This is preparation for network and scaling work, after the local optimization lessons.

Follow the [cluster runbook](../cluster-smoke-test.md), including its approved environment and read-only tooling checks. Two distinct nodes must each expose one full H100. Keep placement logs private and share only sanitized summaries.

## Concepts and code path

The launcher starts distributed workers; common helpers identify ranks and devices, initialize NCCL, and collect placement information. A known all-reduce checks that the participants can exchange and sum tensors. The topology check prevents accidentally treating two processes on one node as the requested two-node experiment.

## Practice

Inspect command options locally, then submit through the two-node launcher. The launcher owns process startup; do not add another distributed launcher inside the lab invocation.

```bash
umask 077
python labs/00_cluster_preflight.py --help
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

## Check your results

Require `topology` and `all_reduce` correctness, world size two, and one H100 per rank. A successful import or scheduler submission without a completed result is not a passed platform check.

## Investigate the behavior

Write the invariants that must remain fixed in a later comparison: device variant, rank count, global work, dtype, environment, and timing boundary. Which of these would invalidate a direct baseline/candidate ratio if changed?

## If something goes wrong

Resolve placement failures with the scheduler owner and communication failures with the supported NCCL/network procedure. Do not tune infrastructure or substitute a different topology merely to obtain a passing result.

## Takeaways and next step

Preflight separates platform readiness from application performance. Preserve its result with your experiment record, then proceed to the fixed-work scaling and overlap experiments in Labs 08 and 13. Labs 01 and 02 are prerequisite timing refreshers if their measurement boundaries are not yet clear.
