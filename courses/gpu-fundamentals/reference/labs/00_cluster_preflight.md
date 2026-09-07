# Lab 00: Verify the two-node H100 platform

Before interpreting GPU timings, establish that the program is running on the platform you think it is. This lab checks process placement, device visibility, and communication across the two one-GPU nodes. It is a platform acceptance exercise, not a benchmark: a successful import on the login node cannot replace a successful allocation and collective on both compute nodes.

## Before you start

**Theory preparation:** Read Lesson 12 for Slurm/rank placement, process groups and known-value collectives, and Lesson 1 for readiness checks. Run this preflight before Lesson 12’s two-node experiment; it is not required for the introductory single-GPU labs.

Activate this course's approved environment and follow the [cluster smoke runbook](../cluster-smoke-test.md). You need two available nodes with one full H100 each. Keep scheduler output private because placement diagnostics can identify infrastructure.

## Concepts and code path

The Slurm launcher creates the distributed processes. Shared helpers read rank information, bind each process to its local GPU, initialize NCCL, and exchange placement information. The lab then reduces a known tensor across ranks. World size counts participating processes; local rank selects a device within one node, so both nodes should use local rank zero here.

## Practice

From this course directory, inspect the options before submitting the bounded smoke job. Do not run the distributed program as two unrelated manual Python processes.

```bash
umask 077
python labs/00_cluster_preflight.py --help
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

## Check your results

Require two distinct nodes, world size two, one visible non-MIG H100 per rank, and a correct all-reduce. The JSON correctness fields include `two_distinct_nodes` and `all_reduce`; the presence of a result file alone does not establish success.

## Investigate the behavior

Draw one process-to-device arrow on each node and one communication edge between them. Explain why successful local CUDA execution does not prove that NCCL can communicate across the inter-node network.

## If something goes wrong

A placement failure belongs to the allocation or launcher; missing CUDA belongs to the environment/device path; a collective timeout requires the cluster owner's network and NCCL investigation. Preserve the original failure and do not change cluster settings yourself.

## Takeaways and next step

You now have an explicit execution topology on which the collective experiment can depend. Repeat preflight after changing the environment or placement. In the lesson sequence, Lab 01 has already established a single-GPU baseline; proceed to Lab 06's bounded two-node communication experiment and compare it with that local context.
