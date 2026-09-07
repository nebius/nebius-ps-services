# Lab 00: Verify the distributed training allocation

Training correctness depends on each process owning the intended device and participating in the same communication group. This lab verifies the two-node, one-H100-per-node platform before DDP, FSDP2, or parallelism experiments. You will establish placement and communication readiness separately from whether a model fits, converges, or scales efficiently on that platform.

## Before you start

**Theory preparation:** Read Lesson 11 for distributed state and collectives, plus the prerequisite Optimizations Lesson 11 network workshop and Fundamentals Lesson 12 rank/launcher model. Complete this check before the distributed training labs.

Activate the Training environment and follow the [cluster runbook](../cluster-smoke-test.md). This particular lab requires two ranks on distinct nodes; the single-GPU training labs perform their own device checks.

## Concepts and code path

The shared `common.py` helpers validate H100 visibility, map ranks to devices, initialize NCCL, and gather node identities. The lab checks the declared two-node topology and reduces known values. Model construction and loss computation are deliberately absent so that a platform failure can be diagnosed independently of training logic.

## Practice

Use the course's two-node launcher from this directory. Keep scheduler logs private and do not wrap the invocation in another launcher that would multiply the process count.

```bash
umask 077
python labs/00_cluster_preflight.py --help
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

## Check your results

Require `two_nodes` and `nccl_all_reduce`, world size two, and two distinct hosts in the private placement evidence. Successful job submission without a completed correctness result is not acceptance.

## Investigate the behavior

Draw the rank/device mapping and explain why each node uses local rank zero. Which later failures could still occur after this check passes, such as model memory exhaustion or mismatched collective order?

## If something goes wrong

Resolve incorrect allocation or missing devices through the cluster owner. Preserve communication errors and environment identity. Do not alter network configuration, sharing modes, or drivers as part of this learner exercise.

## Takeaways and next step

Platform readiness is necessary but does not validate a training algorithm. Continue to DDP and FSDP2 using the already-understood single-GPU training step; revisit the tiny-model and loss-mask labs if needed.
